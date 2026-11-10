"""Контракт `GET /api/v1/analytics`: сессия, видимость, период и форма ответа. Без БД и сети.

Агрегаты считает SQL — их проверяет `tests/db/test_analytics_adapter.py`. Здесь фейк репозитория
отдаёт заготовленный `Analytics` и запоминает выборку, с которой его вызвала ручка.
"""

import datetime as dt
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_installation_catalog, get_token_provider
from app.api.factory import create_app
from app.config import Settings
from app.domain.analytics import (
    Analytics,
    AnalyticsFilter,
    FindingsDayCount,
    RepositoryAnalytics,
    RunDurations,
)
from app.domain.entities import (
    InstallationRepository,
    Repository,
    RepositoryPermissions,
)
from app.domain.enums import FindingCategory, FindingSeverity, Provider
from app.domain.ids import new_id
from app.domain.vcs_errors import VcsError

from .fakes.auth import sign_in_with_session
from .fakes.installation_catalog import FakeInstallationCatalog
from .fakes.oauth import FakeOAuthProvider
from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)
PATH = "/api/v1/analytics"
FROM = datetime(2026, 10, 1, tzinfo=UTC)
TO = datetime(2026, 11, 1, tzinfo=UTC)
PERIOD = {"created_from": "2026-10-01T00:00:00Z", "created_to": "2026-11-01T00:00:00Z"}

READ = RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)
# Пользователю на GitHub видны API и WEB; SECRET подключён, но не виден.
API = InstallationRepository("101", "octo-org/api", "main", READ)
WEB = InstallationRepository("102", "octo-org/web", "main", READ)
SECRET = InstallationRepository("202", "octo-org/secret", "main", READ)


class World:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.repos: dict[str, Repository] = {}
        for source in (API, WEB, SECRET):
            repository = Repository(
                id=new_id(),
                provider=Provider.GITHUB,
                provider_id=source.provider_id,
                full_name=source.full_name,
                default_branch="main",
                auto_review_enabled=True,
                created_at=FROM,
                updated_at=FROM,
                connected_at=FROM,
            )
            self.uow.repositories.add(repository)
            self.repos[source.full_name] = repository

    def id(self, name: str) -> UUID:
        return self.repos[name].id

    @property
    def filters(self) -> list[AnalyticsFilter]:
        return self.uow.review_runs.analytics_filters


def _client(world: World, catalog: FakeInstallationCatalog | None = None) -> TestClient:
    app = create_app(SETTINGS)
    cookies = sign_in_with_session(app, world.uow)
    app.dependency_overrides[get_installation_catalog] = lambda: catalog or FakeInstallationCatalog([API, WEB])
    app.dependency_overrides[get_token_provider] = lambda: FakeOAuthProvider()
    return TestClient(app, cookies=cookies)


def _get(world: World, **params: Any) -> dict[str, Any]:
    response = _client(world).get(PATH, params={**PERIOD, **params})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


EMPTY_BODY = {
    "created_from": "2026-10-01T00:00:00Z",
    "created_to": "2026-11-01T00:00:00Z",
    "findings_by_day": [],
    "runs": {"count": 0, "average_duration_seconds": None, "p95_duration_seconds": None},
    "by_repository": [],
}


def test_repository_gets_only_visible_repositories_and_the_period() -> None:
    world = World()

    _get(world)

    assert world.filters == [AnalyticsFilter(frozenset({world.id("octo-org/api"), world.id("octo-org/web")}), FROM, TO)]


def test_repository_filter_narrows_the_selection_to_that_repository() -> None:
    world = World()

    _get(world, repository_id=str(world.id("octo-org/web")))

    assert world.filters == [AnalyticsFilter(frozenset({world.id("octo-org/web")}), FROM, TO)]


@pytest.mark.parametrize("repo", ["octo-org/secret", None])
def test_invisible_or_unknown_repository_is_404(repo: str | None) -> None:
    world = World()
    repository_id = str(world.id(repo)) if repo else str(new_id())

    response = _client(world).get(PATH, params={**PERIOD, "repository_id": repository_id})

    assert response.status_code == 404
    assert response.json()["code"] == "REPOSITORY_NOT_FOUND"
    assert world.filters == []


def test_nothing_visible_gives_the_empty_full_shape() -> None:
    world = World()
    # На GitHub пользователь видит только неподключённый репозиторий.
    stranger = FakeInstallationCatalog([InstallationRepository("999", "octo-org/other", "main", READ)])

    response = _client(world, stranger).get(PATH, params=PERIOD)

    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY
    # Пустая выборка — пустой ответ без запроса в базу: это проверяет адаптер.
    assert [f.repository_ids for f in world.filters] == [frozenset()]


def test_analytics_is_serialized_with_its_field_names() -> None:
    world = World()
    api, web = world.id("octo-org/api"), world.id("octo-org/web")
    world.uow.review_runs.analytics_result = Analytics(
        findings_by_day=(
            FindingsDayCount(dt.date(2026, 10, 3), FindingCategory.SECURITY, FindingSeverity.HIGH, 2),
            FindingsDayCount(dt.date(2026, 10, 4), FindingCategory.READABILITY, FindingSeverity.LOW, 1),
        ),
        runs=RunDurations(6, 250 / 6, 87.5),
        by_repository=(
            RepositoryAnalytics(api, "octo-org/api", 5, 40.0, 88.0, 3),
            RepositoryAnalytics(web, "octo-org/web", 1, 50.0, 50.0, 0),
        ),
    )

    body = _get(world)

    assert body == {
        "created_from": "2026-10-01T00:00:00Z",
        "created_to": "2026-11-01T00:00:00Z",
        "findings_by_day": [
            {"date": "2026-10-03", "category": "security", "severity": "high", "count": 2},
            {"date": "2026-10-04", "category": "readability", "severity": "low", "count": 1},
        ],
        "runs": {"count": 6, "average_duration_seconds": 250 / 6, "p95_duration_seconds": 87.5},
        "by_repository": [
            {
                "repository_id": str(api),
                "full_name": "octo-org/api",
                "runs_count": 5,
                "average_duration_seconds": 40.0,
                "p95_duration_seconds": 88.0,
                "findings_count": 3,
            },
            {
                "repository_id": str(web),
                "full_name": "octo-org/web",
                "runs_count": 1,
                "average_duration_seconds": 50.0,
                "p95_duration_seconds": 50.0,
                "findings_count": 0,
            },
        ],
    }


def test_default_period_is_the_last_30_days() -> None:
    world = World()
    now = datetime.now(UTC)

    response = _client(world).get(PATH)

    assert response.status_code == 200, response.text
    (selection,) = world.filters
    assert selection.created_to - selection.created_from == timedelta(days=30)
    assert abs(selection.created_to - now) < timedelta(minutes=1)
    body = response.json()
    assert (datetime.fromisoformat(body["created_from"]), datetime.fromisoformat(body["created_to"])) == (
        selection.created_from,
        selection.created_to,
    )


def test_missing_created_from_is_30_days_before_created_to() -> None:
    world = World()

    response = _client(world).get(PATH, params={"created_to": "2026-11-01T00:00:00Z"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["created_from"], body["created_to"]) == ("2026-10-02T00:00:00Z", "2026-11-01T00:00:00Z")
    assert [(f.created_from, f.created_to) for f in world.filters] == [(datetime(2026, 10, 2, tzinfo=UTC), TO)]


@pytest.mark.parametrize(
    ("params", "error"),
    [
        (
            {"created_from": "2026-11-02T00:00:00Z", "created_to": "2026-11-01T00:00:00Z"},
            {"where": "query.created_to", "reason": "начало диапазона позже его конца"},
        ),
        # Без created_to конец — сейчас: виновато начало в будущем, а не created_to, которого нет.
        ({"created_from": "2999-01-01T00:00:00Z"}, {"where": "query.created_from", "reason": "начало диапазона позже его конца"}),
        (
            {"created_from": "2026-01-01T00:00:00Z", "created_to": "2026-07-01T00:00:01Z"},
            {"where": "query.created_from", "reason": "период длиннее 180 дней"},
        ),
        ({"created_from": "2000-01-01T00:00:00Z"}, {"where": "query.created_from", "reason": "период длиннее 180 дней"}),
        ({"created_from": "2026-11-01T00:00:00"}, {"where": "query.created_from", "reason": "ожидается время с зоной"}),
        ({"repository_id": "nope"}, {"where": "query.repository_id", "reason": "ожидается UUID"}),
    ],
)
def test_invalid_query_is_422_before_asking_github(params: dict[str, str], error: dict[str, str]) -> None:
    world = World()
    catalog = FakeInstallationCatalog([API])

    response = _client(world, catalog).get(PATH, params=params)

    assert response.status_code == 422, response.text
    assert catalog.calls == []
    assert world.filters == []
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["errors"] == [error]


@pytest.mark.parametrize("created_from", ["2026-01-01T00:00:00Z", "2026-06-30T00:00:00Z"])
def test_exactly_180_days_and_an_empty_period_are_accepted(created_from: str) -> None:
    response = _client(World()).get(PATH, params={"created_from": created_from, "created_to": "2026-06-30T00:00:00Z"})

    assert response.status_code == 200, response.text


def test_end_at_the_start_of_the_calendar_is_422_not_500() -> None:
    """30 дней до created_to раньше начала календаря: 422 в created_to, как у времени вне диапазона."""
    response = _client(World()).get(PATH, params={"created_to": "0001-01-05T00:00:00Z"})

    assert response.status_code == 422, response.text
    assert response.json()["errors"] == [{"where": "query.created_to", "reason": "время вне допустимого диапазона"}]


def test_without_session_is_401() -> None:
    response = TestClient(create_app(SETTINGS)).get(PATH)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_github_failure_is_502() -> None:
    world = World()
    catalog = FakeInstallationCatalog(error=VcsError("GitHub недоступен"))

    response = _client(world, catalog).get(PATH, params=PERIOD)

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"
    assert world.filters == []
