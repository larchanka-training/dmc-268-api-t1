"""Демо-режим на ручках (change `demo-mode`): анонимное чтение, только чтение, вошедший видит своё.

Без базы и сети: фейковые unit of work, каталог установок и провайдер входа. Демо-данные — тот
же набор, что пишет `python -m app.worker.seed_demo`.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_clock,
    get_installation_catalog,
    get_run_changes,
    get_token_provider,
    get_unit_of_work,
    get_unit_of_work_factory,
    get_untraced_unit_of_work_factory,
)
from app.api.factory import create_app
from app.api.session import SESSION_COOKIE, current_session, current_user
from app.application.ports.run_changes import RunChangesLost
from app.application.use_cases.seed_demo import demo_dataset, seed_demo
from app.config import Settings
from app.domain.entities import InstallationRepository, RepositoryPermissions

from .fakes.auth import sign_in_with_session
from .fakes.installation_catalog import FakeInstallationCatalog
from .fakes.oauth import FakeOAuthProvider
from .fakes.run_changes import FakeRunChanges
from .fakes.unit_of_work import FakeUnitOfWork

ORIGIN = "https://review.example"
NOW = datetime.now(UTC)
DATASET = demo_dataset(NOW)
COMPLETED, FAILED, ANALYSING = DATASET.runs
API_REPOSITORY = DATASET.repositories[0]
REAL = InstallationRepository(
    "101", "octo-org/api", "main", RepositoryPermissions(admin=True, maintain=False, push=False, triage=False)
)


def _settings(*, demo_mode: bool) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="test-secret",
        public_origin=ORIGIN,
        demo_mode=demo_mode,
    )


class Demo:
    def __init__(self, *, demo_mode: bool = True, seeded: bool = True) -> None:
        self.uow = FakeUnitOfWork()
        if seeded:
            seed_demo(self.uow, DATASET)
        # Каталог помнит каждый запрос к GitHub: у анонимного демо их быть не должно.
        self.catalog = FakeInstallationCatalog([REAL])
        self.app = create_app(_settings(demo_mode=demo_mode))
        self.app.dependency_overrides[get_unit_of_work_factory] = lambda: lambda: self.uow
        self.app.dependency_overrides[get_unit_of_work] = lambda: self.uow
        self.app.dependency_overrides[get_installation_catalog] = lambda: self.catalog
        self.app.dependency_overrides[get_token_provider] = lambda: FakeOAuthProvider()

    def client(self, cookies: dict[str, str] | None = None) -> TestClient:
        # https: cookie с `Secure` клиент по http обратно не отправит.
        return TestClient(self.app, base_url=ORIGIN, cookies=cookies, follow_redirects=False)


@pytest.fixture
def demo() -> Demo:
    return Demo()


def test_reviews_without_session_show_the_demo_runs(demo: Demo) -> None:
    response = demo.client().get("/api/v1/reviews")

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {str(r.id) for r in DATASET.runs}
    assert demo.catalog.calls == []


def test_review_card_and_steps_without_session(demo: Demo) -> None:
    client = demo.client()

    card = client.get(f"/api/v1/reviews/{COMPLETED.id}")
    steps = client.get(f"/api/v1/reviews/{COMPLETED.id}/steps")

    assert (card.status_code, steps.status_code) == (200, 200)
    assert len(steps.json()["items"]) == 5
    assert demo.catalog.calls == []


def test_demo_diff_is_unavailable_rather_than_fetched(demo: Demo) -> None:
    response = demo.client().get(f"/api/v1/reviews/{COMPLETED.id}/diff")

    assert (response.status_code, response.json()["code"]) == (409, "DIFF_UNAVAILABLE")
    assert demo.catalog.calls == []


def test_repositories_without_session_are_demo_and_read_only(demo: Demo) -> None:
    response = demo.client().get("/api/v1/repositories")

    assert response.status_code == 200
    body = response.json()
    assert [item["full_name"] for item in body["items"]] == ["demo-org/payments-api", "demo-org/web-cabinet"]
    assert all(item["connected"] and not item["can_connect"] for item in body["items"])
    assert body["install_url"] is None
    assert demo.catalog.calls == []


def test_repository_card_settings_rules_and_pull_requests(demo: Demo) -> None:
    client = demo.client()
    base = f"/api/v1/repositories/{API_REPOSITORY.id}"

    responses = [client.get(base + suffix) for suffix in ("", "/settings", "/rules", "/pull-requests")]

    assert [r.status_code for r in responses] == [200, 200, 200, 200]
    assert responses[2].json()["status"] == "absent"
    assert demo.catalog.calls == []


def test_analytics_without_session(demo: Demo) -> None:
    period = {
        "created_from": (NOW - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_to": (NOW + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    response = demo.client().get("/api/v1/analytics", params=period)

    assert response.status_code == 200
    assert demo.catalog.calls == []


def test_me_without_session_is_the_demo_user(demo: Demo) -> None:
    response = demo.client().get("/api/v1/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert (body["login"], body["is_demo"], body["balance_eur"]) == ("demo", True, "10")
    assert [r["full_name"] for r in body["paid_repositories"]] == ["demo-org/payments-api", "demo-org/web-cabinet"]


def test_change_without_session_is_still_unauthenticated(demo: Demo) -> None:
    response = demo.client().post(
        "/api/v1/reviews",
        json={"repository_id": str(API_REPOSITORY.id), "pull_request_number": 12},
        headers={"Origin": ORIGIN},
    )

    assert (response.status_code, response.json()["code"]) == (401, "UNAUTHENTICATED")
    assert len(demo.uow.review_runs.list_unfinished()) == 1


def test_invalid_cookie_is_deleted_and_demo_is_served(demo: Demo) -> None:
    response = demo.client(cookies={SESSION_COOKIE: "unknown"}).get("/api/v1/reviews")

    assert response.status_code == 200
    assert 'session=""' in response.headers["set-cookie"] and "Max-Age=0" in response.headers["set-cookie"]


def test_without_demo_mode_reading_needs_a_session() -> None:
    response = Demo(demo_mode=False).client().get("/api/v1/reviews")

    assert (response.status_code, response.json()["code"]) == (401, "UNAUTHENTICATED")


def test_demo_mode_without_seed_behaves_as_off() -> None:
    response = Demo(seeded=False).client().get("/api/v1/auth/me")

    assert (response.status_code, response.json()["code"]) == (401, "UNAUTHENTICATED")


def _signed_in_for_real(demo: Demo) -> dict[str, str]:
    """Живая сессия без подмены `current_session`: приоритет сессии над демо решает сам код."""
    cookies = sign_in_with_session(demo.app, demo.uow)
    demo.app.dependency_overrides.pop(current_session)
    demo.app.dependency_overrides.pop(current_user)
    return cookies


def test_signed_in_user_does_not_see_demo_runs(demo: Demo) -> None:
    cookies = _signed_in_for_real(demo)

    reviews = demo.client(cookies).get("/api/v1/reviews")
    me = demo.client(cookies).get("/api/v1/auth/me")

    assert reviews.status_code == 200
    assert reviews.json()["items"] == []
    assert demo.catalog.calls == ["list_repositories"]
    assert me.json()["is_demo"] is False


def test_after_logout_reading_is_demo_and_changing_is_unauthenticated(demo: Demo) -> None:
    """Спека `user-auth`, «Выход»: в демо-режиме `GET` с cookie вышедшей сессии — демо, запись — `401`."""
    cookies = _signed_in_for_real(demo)
    client = demo.client(cookies)
    before = client.get("/api/v1/auth/me")

    logout = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
    after = demo.client(cookies).get("/api/v1/auth/me")
    change = demo.client(cookies).post(
        "/api/v1/reviews",
        json={"repository_id": str(API_REPOSITORY.id), "pull_request_number": 12},
        headers={"Origin": ORIGIN},
    )

    assert before.json()["is_demo"] is False
    assert logout.status_code == 204
    assert (after.status_code, after.json()["is_demo"]) == (200, True)
    assert 'session=""' in after.headers["set-cookie"] and "Max-Age=0" in after.headers["set-cookie"]
    assert (change.status_code, change.json()["code"]) == (401, "UNAUTHENTICATED")


def test_event_stream_without_session_stays_open(demo: Demo) -> None:
    """Демо-пользователя нечем отозвать: проверки сессии раз в 5 с поток не закрывают."""
    clock = {"now": 0.0}

    async def lost_after_twenty_seconds(timeout: float) -> bool:
        clock["now"] += min(timeout, 1.0)
        if clock["now"] >= 20:
            raise RunChangesLost
        return False

    demo.app.dependency_overrides[get_untraced_unit_of_work_factory] = lambda: lambda: demo.uow
    demo.app.dependency_overrides[get_run_changes] = lambda: FakeRunChanges(lost_after_twenty_seconds)
    demo.app.dependency_overrides[get_clock] = lambda: lambda: clock["now"]

    response = demo.client().get("/api/v1/events")

    assert response.status_code == 200
    # Поток пережил проверки сессии на 5, 10 и 15 с и успел отдать ping, закрыл его только обрыв источника.
    assert "event: ping" in response.text
    assert clock["now"] == 20


@pytest.mark.parametrize("available", [True, False], ids=["stream", "source_lost"])
def test_event_stream_with_unknown_session_deletes_cookie(demo: Demo, available: bool) -> None:
    """Как у остальных `GET` в демо: cookie неизвестной сессии удаляется и в ответе потока."""

    async def lost(timeout: float) -> bool:
        raise RunChangesLost

    demo.app.dependency_overrides[get_untraced_unit_of_work_factory] = lambda: lambda: demo.uow
    demo.app.dependency_overrides[get_run_changes] = lambda: FakeRunChanges(lost, available=available)

    response = demo.client({SESSION_COOKIE: "unknown"}).get("/api/v1/events")

    assert response.status_code == 200
    assert response.text.startswith("retry: 3000")
    assert 'session=""' in response.headers["set-cookie"] and "Max-Age=0" in response.headers["set-cookie"]
