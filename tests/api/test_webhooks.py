"""Эндпоинт `POST /api/v1/webhooks/{provider}` на TestClient с фейками в контейнере.

Контейнеры подменяются целиком: `app.state.container` получает заглушку с
фейковыми портами, поэтому тесты отвечают на вопрос «что делает HTTP-слой» —
подпись, коды ответов, маппинг исходов use case в контракт `openapi.yaml` —
и не трогают базу и брокер. Подписи считаются здесь независимо
(hmac + hashlib), а не функцией `verify_hmac`, чтобы тест не сверял
реализацию с ней же.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from httpx import Client, Response
from pydantic import ValidationError

from app.api.factory import create_app
from app.config import Settings
from app.domain.vcs_errors import VcsError

from ..fakes import FakeQueue, FakeUow, FakeVcs, webhook_payload

SECRET = "test-webhook-secret"
SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"


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
        rabbitmq_url="amqp://guest:guest@localhost//",
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
    provider: str = "github",
    event: str | None = "pull_request",
) -> Response:
    if body is None:
        body = json.dumps(webhook_payload(action)).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers[SIGNATURE_HEADER] = sign(body)
    elif signature is not None:
        headers[SIGNATURE_HEADER] = signature
    if event is not None:
        headers[EVENT_HEADER] = event
    return client.post(f"/api/v1/webhooks/{provider}", content=body, headers=headers)


def test_valid_opened_returns_202_with_review_job_projection() -> None:
    client, container = make_client()
    response = post_webhook(client, "opened")
    assert response.status_code == 202
    (run,) = container.uow.review_runs.added
    job = response.json()
    assert job["id"] == str(run.id)
    assert job["provider"] == "github"
    assert job["status"] == "queued"
    assert job["trigger"] == "webhook"
    assert job["headCommitSha"] == run.head_sha
    assert container.uow.commits == 1
    assert len(container.queue.jobs) == 1


def test_valid_synchronize_returns_202_and_enqueues_job() -> None:
    client, container = make_client()
    response = post_webhook(client, "synchronize")
    assert response.status_code == 202
    assert container.queue.jobs[-1].action == "synchronize"


def test_duplicate_delivery_returns_202_with_existing_review_job() -> None:
    """Контракт: «ReviewJob создан или найден существующий»."""
    client, container = make_client()
    first = post_webhook(client, "opened")
    (existing,) = container.uow.review_runs.added

    second = post_webhook(client, "opened")
    assert second.status_code == 202
    assert second.json()["id"] == str(existing.id)
    assert second.json()["id"] == first.json()["id"]
    assert len(container.uow.review_runs.added) == 1
    assert len(container.queue.jobs) == 1


def test_missing_signature_is_rejected_with_401() -> None:
    client, container = make_client()
    response = post_webhook(client, "opened", signature=None)
    assert response.status_code == 401
    assert response.json() == {
        "code": "WEBHOOK_SIGNATURE_INVALID",
        "message": "invalid webhook signature",
    }
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


def test_signed_delivery_of_another_event_type_is_ignored() -> None:
    """Тип события — заголовок, а не форма payload'а: тело `pull_request_target`
    неотличимо от `pull_request`, но прогон создаёт только `pull_request`."""
    client, container = make_client()
    response = post_webhook(client, "opened", event="pull_request_target")
    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert container.uow.review_runs.added == []
    assert container.queue.jobs == []


def test_delivery_without_event_header_is_ignored() -> None:
    client, container = make_client()
    response = post_webhook(client, "opened", event=None)
    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert container.uow.review_runs.added == []
    assert container.queue.jobs == []


def test_foreign_event_is_ignored_before_the_body_is_parsed() -> None:
    """Чужое событие отсекается до разбора: не-JSON тело всё равно ignored."""
    client, container = make_client()
    response = post_webhook(client, event="issue_comment", body=b"not json at all")
    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    assert container.queue.jobs == []


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
    client, container = make_client(
        uow=FakeUow(), vcs=FakeVcs(error=VcsError("HTTP 502"))
    )
    response = post_webhook(client, "opened")
    assert response.status_code == 502
    assert response.json() == {
        "code": "SCM_UNAVAILABLE",
        "message": "VCS provider unavailable",
    }
    assert container.uow.review_runs.added == []
    assert container.queue.jobs == []


def test_unsupported_provider_returns_501() -> None:
    """GitLab приёмником ещё не поддержан: маршрут контрактный, адаптера нет."""
    client, container = make_client()
    response = post_webhook(client, "opened", provider="gitlab")
    assert response.status_code == 501
    assert response.json()["code"] == "PROVIDER_NOT_SUPPORTED"
    assert container.uow.review_runs.added == []
    assert container.queue.jobs == []


def test_empty_secret_does_not_reach_the_endpoint() -> None:
    """Пустой секрет отклоняется на старте настроек: приложение без секрета
    не поднимается, «валидная» подпись с пустым ключом невозможна by design."""
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://test:test@localhost/test",
            rabbitmq_url="amqp://guest:guest@localhost//",
            github_webhook_secret="",
        )
