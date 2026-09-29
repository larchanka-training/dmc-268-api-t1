"""Эндпоинт `POST /webhooks/github` на TestClient с фейками в контейнере.

Контейнеры подменяются целиком: `app.state.container` получает заглушку с
фейковыми портами, поэтому тесты отвечают на вопрос «что делает HTTP-слой» —
подпись, коды ответов, маппинг исходов use case — и не трогают базу и брокер.
Подписи считаются здесь независимо (hmac + hashlib), а не функцией `verify_hmac`,
чтобы тест не сверял реализацию с ней же.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass

from fastapi.testclient import TestClient
from httpx import Client, Response

from app.api.factory import create_app
from app.config import Settings

from ..fakes import FakeQueue, FakeUow, FakeVcs, webhook_payload

SECRET = "test-webhook-secret"
SIGNATURE_HEADER = "X-Hub-Signature-256"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@dataclass
class StubContainer:
    """Тот же интерфейс, что у `Container`, но с фейками вместо адаптеров."""

    settings: Settings
    uow: FakeUow
    vcs: FakeVcs
    queue: FakeQueue

    def unit_of_work(self) -> FakeUow:
        return self.uow

    def vcs_gateway(self) -> FakeVcs:
        return self.vcs

    def job_queue(self) -> FakeQueue:
        return self.queue


def make_client(
    uow: FakeUow | None = None,
    vcs: FakeVcs | None = None,
    queue: FakeQueue | None = None,
    secret: str = SECRET,
    registered: bool = True,
) -> tuple[TestClient, StubContainer]:
    settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        github_webhook_secret=secret,
    )
    uow = uow if uow is not None else FakeUow()
    if registered:
        from app.domain.enums import Provider

        from ..fakes import REGISTERED_PROVIDER_ID, a_repository

        uow.repositories.add(a_repository())
        assert (Provider.GITHUB, REGISTERED_PROVIDER_ID) in uow.repositories.stored
    container = StubContainer(
        settings=settings,
        uow=uow,
        vcs=vcs if vcs is not None else FakeVcs(),
        queue=queue if queue is not None else FakeQueue(),
    )
    app = create_app(settings)
    app.state.container = container
    return TestClient(app), container


def post_webhook(
    client: Client,
    action: str = "opened",
    *,
    signature: str | None = "auto",
    body: bytes | None = None,
) -> Response:
    if body is None:
        body = json.dumps(webhook_payload(action)).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers[SIGNATURE_HEADER] = sign(body)
    elif signature is not None:
        headers[SIGNATURE_HEADER] = signature
    return client.post("/webhooks/github", content=body, headers=headers)


def test_valid_opened_returns_202_and_creates_records_and_job() -> None:
    client, container = make_client()
    response = post_webhook(client, "opened")
    assert response.status_code == 202
    assert response.json()["status"] == "created"
    (run,) = container.uow.review_runs.added
    assert response.json()["review_run_id"] == str(run.id)
    assert container.uow.commits == 1
    assert len(container.queue.jobs) == 1


def test_valid_synchronize_returns_202_and_enqueues_job() -> None:
    client, container = make_client()
    response = post_webhook(client, "synchronize")
    assert response.status_code == 202
    assert container.queue.jobs[-1].action == "synchronize"


def test_missing_signature_is_rejected_with_401() -> None:
    client, container = make_client()
    response = post_webhook(client, "opened", signature=None)
    assert response.status_code == 401
    assert container.uow.review_runs.added == []
    assert container.queue.jobs == []


def test_wrong_signature_is_rejected_with_401() -> None:
    client, container = make_client()
    response = post_webhook(client, "opened", signature=sign(b"other body"))
    assert response.status_code == 401
    assert container.uow.review_runs.added == []
    assert container.queue.jobs == []


def test_signature_without_sha256_prefix_is_rejected_with_401() -> None:
    client, _ = make_client()
    body = json.dumps(webhook_payload()).encode("utf-8")
    response = post_webhook(client, signature=sign(body)[len("sha256=") :], body=body)
    assert response.status_code == 401


def test_signature_is_checked_before_the_body_is_parsed() -> None:
    """Неверная подпись + тело не JSON — 401, а не ошибка разбора."""
    client, container = make_client()
    response = post_webhook(client, signature=sign(b"wrong key"), body=b"not json at all")
    assert response.status_code == 401
    assert container.queue.jobs == []


def test_valid_signature_with_non_json_body_is_ignored() -> None:
    client, container = make_client()
    response = post_webhook(client, body=b"not json at all")
    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert container.uow.review_runs.added == []


def test_valid_signature_with_non_dict_json_is_ignored() -> None:
    client, _ = make_client()
    response = post_webhook(client, body=b"[1, 2, 3]")
    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}


def test_reopened_returns_202_ignored_without_records() -> None:
    client, container = make_client()
    response = post_webhook(client, "reopened")
    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert container.uow.merge_requests.added == []
    assert container.uow.review_runs.added == []
    assert container.queue.jobs == []


def test_unregistered_repository_returns_202_ignored() -> None:
    client, container = make_client(registered=False)
    response = post_webhook(client, "opened")
    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert container.uow.review_runs.added == []
    assert container.queue.jobs == []


def test_vcs_failure_returns_502_without_records() -> None:
    from app.application.ports.vcs_gateway import VcsError

    client, container = make_client(uow=FakeUow(), vcs=FakeVcs(error=VcsError("HTTP 502")))
    response = post_webhook(client, "opened")
    assert response.status_code == 502
    assert container.uow.review_runs.added == []
    assert container.queue.jobs == []


def test_empty_secret_rejects_everything() -> None:
    """Пустой секрет не подписывает ничего: даже «валидная» подпись отклоняется."""
    client, _ = make_client(secret="")
    body = json.dumps(webhook_payload()).encode("utf-8")
    response = post_webhook(client, signature=sign(body, secret=""), body=body)
    assert response.status_code == 401
