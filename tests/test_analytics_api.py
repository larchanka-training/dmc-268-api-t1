"""Контракт `GET /api/v1/analytics`: видимость, период, фильтр по репозиторию и форма ответа. Без БД и сети."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_installation_catalog, get_token_provider
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import (
    DiffAnchor,
    Finding,
    InstallationRepository,
    MergeRequest,
    Repository,
    RepositoryPermissions,
    ReviewRun,
)
from app.domain.enums import (
    CoverageReason,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    TriggerSource,
)
from app.domain.enums import ReviewRunStatus as S
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
# Период запросов тестов; прогоны — внутри него.
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
        self.mrs: dict[str, MergeRequest] = {}
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
            merge_request = MergeRequest(
                id=new_id(),
                repository_id=repository.id,
                number=1,
                title="Фича",
                description="",
                author="octocat",
                source_branch="feat",
                target_branch="main",
                head_sha="abc123",
                state=MergeRequestState.OPEN,
                created_at=FROM,
                updated_at=FROM,
            )
            self.uow.merge_requests.add(merge_request)
            self.repos[source.full_name] = repository
            self.mrs[source.full_name] = merge_request

    def run(
        self,
        repo: str,
        at: datetime,
        duration: float | None = 10.0,
        *,
        status: S = S.COMPLETED,
        coverage_reason: CoverageReason | None = None,
        findings: tuple[tuple[FindingCategory, FindingSeverity], ...] = (),
    ) -> ReviewRun:
        run = ReviewRun(
            id=new_id(),
            merge_request_id=self.mrs[repo].id,
            head_sha="abc123",
            status=status,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=at,
            created_at=at,
            updated_at=at,
            duration_seconds=duration,
            coverage_reason=coverage_reason,
        )
        self.uow.review_runs._by_id[run.id] = run
        for line, (category, severity) in enumerate(findings, start=1):
            self.uow.findings.add(
                Finding(
                    id=new_id(),
                    review_run_id=run.id,
                    anchor=DiffAnchor(file_path="app.py", side=DiffSide.NEW, old_line=None, new_line=line),
                    category=category,
                    severity=severity,
                    message="Замечание",
                    created_at=at,
                    updated_at=at,
                )
            )
        return run


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


SEC_HIGH = (FindingCategory.SECURITY, FindingSeverity.HIGH)
SEC_LOW = (FindingCategory.SECURITY, FindingSeverity.LOW)
READ_LOW = (FindingCategory.READABILITY, FindingSeverity.LOW)


def test_findings_are_counted_per_utc_day_category_and_severity() -> None:
    world = World()
    # 23:30 по Москве 3 октября — это ещё 3 октября в UTC (20:30Z).
    world.run("octo-org/api", datetime(2026, 10, 3, 20, 30, tzinfo=UTC), findings=(SEC_HIGH, SEC_HIGH, SEC_LOW))
    world.run("octo-org/web", datetime(2026, 10, 3, 23, 59, tzinfo=UTC), findings=(READ_LOW,))
    world.run("octo-org/api", datetime(2026, 10, 4, 0, 0, tzinfo=UTC), findings=(SEC_HIGH,))

    body = _get(world)

    assert body["findings_by_day"] == [
        {"date": "2026-10-03", "category": "security", "severity": "low", "count": 1},
        {"date": "2026-10-03", "category": "security", "severity": "high", "count": 2},
        {"date": "2026-10-03", "category": "readability", "severity": "low", "count": 1},
        {"date": "2026-10-04", "category": "security", "severity": "high", "count": 1},
    ]
    assert body["created_from"] == "2026-10-01T00:00:00Z"
    assert body["created_to"] == "2026-11-01T00:00:00Z"


def test_durations_overall_and_per_repository_with_p95() -> None:
    world = World()
    at = FROM + timedelta(days=1)
    for seconds in (10.0, 20.0, 30.0, 40.0, 100.0):
        world.run("octo-org/api", at, seconds, findings=(SEC_LOW,))
    world.run("octo-org/web", at, 50.0)

    body = _get(world)

    # percentile_cont(0.95) по 10, 20, 30, 40, 50, 100: позиция 4,75 → 50 + 0,75 · 50.
    assert body["runs"] == {"count": 6, "average_duration_seconds": 250 / 6, "p95_duration_seconds": 87.5}
    assert body["by_repository"] == [
        {
            "repository_id": str(world.repos["octo-org/api"].id),
            "full_name": "octo-org/api",
            "runs_count": 5,
            "average_duration_seconds": 40.0,
            # Позиция 3,8 → 40 + 0,8 · 60.
            "p95_duration_seconds": pytest.approx(88.0),
            "findings_count": 5,
        },
        {
            "repository_id": str(world.repos["octo-org/web"].id),
            "full_name": "octo-org/web",
            "runs_count": 1,
            "average_duration_seconds": 50.0,
            "p95_duration_seconds": 50.0,
            "findings_count": 0,
        },
    ]


def test_only_completed_runs_that_did_review_are_counted() -> None:
    world = World()
    at = FROM + timedelta(days=1)
    world.run("octo-org/api", at, 10.0, findings=(SEC_LOW,))
    world.run("octo-org/api", at, 0.1, coverage_reason=CoverageReason.BALANCE)
    world.run("octo-org/api", at, 500.0, status=S.FAILED, findings=(SEC_HIGH,))
    world.run("octo-org/api", at, None, status=S.ANALYSING)

    body = _get(world)

    assert body["runs"] == {"count": 1, "average_duration_seconds": 10.0, "p95_duration_seconds": 10.0}
    assert [(d["severity"], d["count"]) for d in body["findings_by_day"]] == [("low", 1)]


def test_repeat_of_a_bot_thread_in_a_later_run_is_not_counted() -> None:
    world = World()
    first = world.run("octo-org/api", FROM + timedelta(days=1), findings=(SEC_HIGH,))
    later = world.run("octo-org/api", FROM + timedelta(days=2), findings=(SEC_HIGH, SEC_LOW))
    (original,) = world.uow.findings.list_for_run(first.id)
    repeat = next(f for f in world.uow.findings.list_for_run(later.id) if f.severity is FindingSeverity.HIGH)
    world.uow.findings.mark_covered(repeat.id, original.id)

    body = _get(world)

    assert [(d["date"], d["severity"], d["count"]) for d in body["findings_by_day"]] == [
        ("2026-10-02", "high", 1),
        ("2026-10-03", "low", 1),
    ]
    assert body["by_repository"][0]["findings_count"] == 2


def test_data_of_a_repository_invisible_on_github_is_not_counted() -> None:
    world = World()
    at = FROM + timedelta(days=1)
    world.run("octo-org/api", at, 10.0, findings=(SEC_LOW,))
    world.run("octo-org/secret", at, 99.0, findings=(SEC_HIGH, SEC_HIGH))

    body = _get(world)

    assert body["runs"]["count"] == 1
    assert [r["full_name"] for r in body["by_repository"]] == ["octo-org/api"]
    assert sum(d["count"] for d in body["findings_by_day"]) == 1


def test_another_user_sees_nothing_of_repositories_they_do_not_see() -> None:
    world = World()
    world.run("octo-org/api", FROM + timedelta(days=1), 10.0, findings=(SEC_LOW,))

    response = _client(world, FakeInstallationCatalog([SECRET])).get(PATH, params=PERIOD)

    assert response.status_code == 200
    assert response.json()["findings_by_day"] == []
    assert response.json()["by_repository"] == []
    assert response.json()["runs"] == {"count": 0, "average_duration_seconds": None, "p95_duration_seconds": None}


def test_repository_filter_scopes_everything_to_that_repository() -> None:
    world = World()
    at = FROM + timedelta(days=1)
    world.run("octo-org/api", at, 10.0, findings=(SEC_LOW,))
    world.run("octo-org/web", at, 30.0, findings=(READ_LOW,))

    body = _get(world, repository_id=str(world.repos["octo-org/web"].id))

    assert body["runs"]["count"] == 1
    assert [r["full_name"] for r in body["by_repository"]] == ["octo-org/web"]
    assert [d["category"] for d in body["findings_by_day"]] == ["readability"]


@pytest.mark.parametrize("repo", ["octo-org/secret", None])
def test_invisible_or_unknown_repository_is_404(repo: str | None) -> None:
    world = World()
    repository_id = str(world.repos[repo].id) if repo else str(new_id())

    response = _client(world).get(PATH, params={**PERIOD, "repository_id": repository_id})

    assert response.status_code == 404
    assert response.json()["code"] == "REPOSITORY_NOT_FOUND"


def test_period_includes_from_and_excludes_to() -> None:
    world = World()
    world.run("octo-org/api", FROM - timedelta(microseconds=1), 1.0)
    world.run("octo-org/api", FROM, 2.0)
    world.run("octo-org/api", TO - timedelta(microseconds=1), 3.0)
    world.run("octo-org/api", TO, 4.0)

    assert _get(world)["runs"]["average_duration_seconds"] == 2.5


def test_default_period_is_the_last_30_days() -> None:
    world = World()
    now = datetime.now(UTC)
    world.run("octo-org/api", now - timedelta(days=29), 10.0)
    world.run("octo-org/api", now - timedelta(days=31), 20.0)

    response = _client(world).get(PATH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runs"]["count"] == 1
    start = datetime.fromisoformat(body["created_from"])
    end = datetime.fromisoformat(body["created_to"])
    assert end - start == timedelta(days=30)
    assert abs(end - now) < timedelta(minutes=1)


def test_missing_created_from_is_30_days_before_created_to() -> None:
    response = _client(World()).get(PATH, params={"created_to": "2026-11-01T00:00:00Z"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["created_from"], body["created_to"]) == ("2026-10-02T00:00:00Z", "2026-11-01T00:00:00Z")


def test_empty_result_has_the_full_shape() -> None:
    body = _get(World())

    assert body == {
        "created_from": "2026-10-01T00:00:00Z",
        "created_to": "2026-11-01T00:00:00Z",
        "findings_by_day": [],
        "runs": {"count": 0, "average_duration_seconds": None, "p95_duration_seconds": None},
        "by_repository": [],
    }


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
    catalog = FakeInstallationCatalog([API])

    response = _client(World(), catalog).get(PATH, params=params)

    assert response.status_code == 422, response.text
    assert catalog.calls == []
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["errors"] == [error]


@pytest.mark.parametrize("created_from", ["2026-01-01T00:00:00Z", "2026-06-30T00:00:00Z"])
def test_exactly_180_days_and_an_empty_period_are_accepted(created_from: str) -> None:
    response = _client(World()).get(PATH, params={"created_from": created_from, "created_to": "2026-06-30T00:00:00Z"})

    assert response.status_code == 200, response.text


def test_without_session_is_401() -> None:
    response = TestClient(create_app(SETTINGS)).get(PATH)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_github_failure_is_502() -> None:
    catalog = FakeInstallationCatalog(error=VcsError("GitHub недоступен"))

    response = _client(World(), catalog).get(PATH, params=PERIOD)

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"
