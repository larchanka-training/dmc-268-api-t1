"""Контракт `GET /api/v1/reviews`: видимость, фильтры, порядок и страницы. Без БД и сети."""

from dataclasses import replace
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
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    TriggerSource,
)
from app.domain.enums import ReviewRunStatus as S
from app.domain.ids import new_id
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_token import ProviderTokenRejected, token_from_grant
from app.domain.vcs_errors import VcsError

from .fakes.auth import SIGNED_IN_SESSION_ID, sign_in_with_session
from .fakes.installation_catalog import FakeInstallationCatalog
from .fakes.oauth import EXPIRING_GRANT, FakeOAuthProvider, rejecting
from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)
PATH = "/api/v1/reviews"
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

READ = RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)
# GitHub по токену пользователя отдаёт API и WEB; SECRET подключён в сервисе, но пользователю не виден.
API = InstallationRepository("101", "octo-org/api", "main", READ)
WEB = InstallationRepository("102", "octo-org/web", "main", READ)
SECRET = InstallationRepository("202", "octo-org/secret", "main", READ)


def _repository(source: InstallationRepository) -> Repository:
    return Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id=source.provider_id,
        full_name=source.full_name,
        default_branch="main",
        auto_review_enabled=True,
        created_at=T0,
        updated_at=T0,
    )


class World:
    """Фейковая база: три подключённых репозитория, из них пользователю видны два."""

    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.api, self.web, self.secret = (_repository(r) for r in (API, WEB, SECRET))
        for repository in (self.api, self.web, self.secret):
            self.uow.repositories.add(repository)

    def merge_request(
        self, repository: Repository, number: int, *, title: str = "Фича", author: str = "octocat", branch: str = "feat"
    ) -> MergeRequest:
        merge_request = MergeRequest(
            id=new_id(),
            repository_id=repository.id,
            number=number,
            title=title,
            description="",
            author=author,
            source_branch=branch,
            target_branch="main",
            head_sha="abc123",
            state=MergeRequestState.OPEN,
            created_at=T0,
            updated_at=T0,
        )
        self.uow.merge_requests.add(merge_request)
        return merge_request

    def run(self, merge_request: MergeRequest, status: S = S.COMPLETED, *, minutes: int = 0) -> ReviewRun:
        at = T0 + timedelta(minutes=minutes)
        run = ReviewRun(
            id=new_id(),
            merge_request_id=merge_request.id,
            head_sha="abc123",
            status=status,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=at,
            created_at=at,
            updated_at=at,
        )
        self.uow.review_runs._by_id[run.id] = run
        return run

    def finding(self, run: ReviewRun) -> None:
        self.uow.findings.add(
            Finding(
                id=new_id(),
                review_run_id=run.id,
                anchor=DiffAnchor(file_path="app.py", side=DiffSide.NEW, old_line=None, new_line=3),
                category=FindingCategory.CORRECTNESS,
                severity=FindingSeverity.HIGH,
                message="Ошибка",
                created_at=T0,
                updated_at=T0,
            )
        )


def _client(
    world: World, catalog: FakeInstallationCatalog | None = None, oauth: FakeOAuthProvider | None = None
) -> TestClient:
    app = create_app(SETTINGS)
    cookies = sign_in_with_session(app, world.uow)
    app.dependency_overrides[get_installation_catalog] = lambda: catalog or FakeInstallationCatalog([API, WEB])
    app.dependency_overrides[get_token_provider] = lambda: oauth or FakeOAuthProvider()
    return TestClient(app, cookies=cookies)


def _list(world: World, **params: Any) -> dict[str, Any]:
    response = _client(world).get(PATH, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _ids(body: dict[str, Any]) -> list[str]:
    return [item["id"] for item in body["items"]]


def test_list_shows_only_runs_of_repositories_visible_on_github() -> None:
    """Прогон подключённого, но невидимого пользователю репозитория в список и в `total` не попадает."""
    world = World()
    visible = world.run(world.merge_request(world.api, 1))
    world.run(world.merge_request(world.secret, 2))

    body = _list(world)

    assert _ids(body) == [str(visible.id)]
    assert body["total"] == 1


def test_list_row_carries_merge_request_repository_and_findings_count() -> None:
    world = World()
    run = world.run(world.merge_request(world.api, 42, title="Кэш"), S.FAILED)
    world.finding(run)
    world.finding(run)

    [item] = _list(world)["items"]

    assert item["findings_count"] == 2
    assert item["created_at"] == "2026-10-20T12:00:00Z"
    assert item["merge_request"]["number"] == 42
    assert item["merge_request"]["url"] == "https://github.com/octo-org/api/pull/42"
    assert item["merge_request"]["repository"]["full_name"] == "octo-org/api"
    assert "findings" not in item


def test_list_is_newest_first_then_by_id_and_paged_with_full_total() -> None:
    world = World()
    merge_request = world.merge_request(world.api, 1)
    old = world.run(merge_request, minutes=0)
    same = sorted((world.run(merge_request, minutes=5), world.run(merge_request, minutes=5)), key=lambda r: r.id)
    new = world.run(merge_request, minutes=10)
    expected = [str(r.id) for r in (new, same[1], same[0], old)]

    assert _ids(_list(world)) == expected
    page = _list(world, limit=2, offset=1)
    assert _ids(page) == expected[1:3]
    assert page["total"] == 4


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"status": ["queued", "failed"]}, {"queued", "failed"}),
        ({"active": "true"}, {"queued", "analysing"}),
        ({"active": "false"}, {"completed", "failed"}),
        ({"active": "true", "status": ["queued", "failed"]}, {"queued"}),
        ({"active": "false", "status": ["analysing"]}, set()),
    ],
)
def test_list_filters_by_status_and_activity(params: dict[str, Any], expected: set[str]) -> None:
    world = World()
    merge_request = world.merge_request(world.api, 1)
    for minutes, status in enumerate((S.QUEUED, S.ANALYSING, S.COMPLETED, S.FAILED)):
        world.run(merge_request, status, minutes=minutes)

    body = _list(world, **params)

    assert {item["status"] for item in body["items"]} == expected
    assert body["total"] == len(expected)


def test_list_filters_by_repository_and_ignores_invisible_ones() -> None:
    """`repository_id` сужает видимые; невидимый или неизвестный id — не 404, а пустой список."""
    world = World()
    api_run = world.run(world.merge_request(world.api, 1))
    web_run = world.run(world.merge_request(world.web, 2), minutes=1)
    world.run(world.merge_request(world.secret, 3), minutes=2)

    assert _ids(_list(world, repository_id=[str(world.api.id)])) == [str(api_run.id)]
    both = _list(world, repository_id=[str(world.api.id), str(world.web.id)])
    assert _ids(both) == [str(web_run.id), str(api_run.id)]
    assert _list(world, repository_id=[str(world.secret.id), str(new_id())]) == {"items": [], "total": 0}


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("кэш", ["cache"]),
        ("ALICE", ["alice"]),
        ("hotfix/", ["branch"]),
        ("42", ["number"]),
        ("#42", ["number"]),
        ("7", ["alice"]),
        ("%", []),
        ("", ["number", "branch", "alice", "cache"]),
    ],
)
def test_list_searches_title_author_branch_or_exact_number(q: str, expected: list[str]) -> None:
    """`q` — подстрока без учёта регистра или точный номер; `%` — просто символ, не шаблон."""
    world = World()
    runs = {
        "cache": world.run(world.merge_request(world.api, 1, title="Кэш ответов"), minutes=0),
        "alice": world.run(world.merge_request(world.api, 2, title="v7", author="alice"), minutes=1),
        "branch": world.run(world.merge_request(world.api, 3, branch="hotfix/login"), minutes=2),
        "number": world.run(world.merge_request(world.api, 42), minutes=3),
    }

    assert _ids(_list(world, q=q)) == [str(runs[name].id) for name in expected]


def test_list_filters_by_exact_author() -> None:
    world = World()
    world.run(world.merge_request(world.api, 1, author="alice-bot"))
    alice = world.run(world.merge_request(world.api, 2, author="alice"), minutes=1)

    assert _ids(_list(world, author="alice")) == [str(alice.id)]


def test_list_period_includes_start_and_excludes_end() -> None:
    world = World()
    merge_request = world.merge_request(world.api, 1)
    runs = [world.run(merge_request, minutes=m) for m in (0, 10, 20)]

    # Граница с другой зоной: 15:10+03:00 — это 12:10Z.
    body = _list(world, created_from="2026-10-20T15:10:00+03:00", created_to="2026-10-20T12:20:00Z")

    assert _ids(body) == [str(runs[1].id)]


def test_list_answers_502_when_github_fails() -> None:
    world = World()
    world.run(world.merge_request(world.api, 1))

    response = _client(world, FakeInstallationCatalog(error=VcsError("GitHub недоступен"))).get(PATH)

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"


def test_invalid_query_is_rejected_before_asking_github() -> None:
    """`422` не стоит запроса в GitHub: видимость считается после проверки параметров."""
    world = World()
    catalog = FakeInstallationCatalog([API])

    response = _client(world, catalog).get(PATH, params={"status": "unknown"})

    assert response.status_code == 422
    assert catalog.calls == []


@pytest.mark.parametrize(
    ("params", "error"),
    [
        (
            {"created_from": "0001-01-01T00:00:00+01:00"},
            {"where": "query.created_from", "reason": "время вне допустимого диапазона"},
        ),
        (
            {"created_to": "9999-12-31T23:59:59-01:00"},
            {"where": "query.created_to", "reason": "время вне допустимого диапазона"},
        ),
        ({"q": "a\x00b"}, {"where": "query.q", "reason": "строка не должна содержать символ NUL"}),
        ({"author": "\x00"}, {"where": "query.author", "reason": "строка не должна содержать символ NUL"}),
    ],
)
def test_values_the_database_cannot_take_are_422_not_500(params: dict[str, str], error: dict[str, str]) -> None:
    """Дата за краем календаря после сдвига зоны и NUL в тексте отказали бы только в базе — ответом 500."""
    catalog = FakeInstallationCatalog([API])

    response = _client(World(), catalog).get(PATH, params=params)

    assert response.status_code == 422
    assert response.json()["errors"] == [error]
    assert catalog.calls == []


# --- токен пользователя: те же ответы, что у GET /repositories/available и GET /events ---


def _expired_but_refreshable(world: World) -> None:
    """Access token сессии истёк давно, refresh token бессрочный: запрос начнёт с обмена."""
    expired = replace(token_from_grant(EXPIRING_GRANT, T0 - timedelta(days=30)), refresh_expires_at=None)
    world.uow.provider_tokens.save(SIGNED_IN_SESSION_ID, expired)


def _assert_closed_with_401(response: Any, world: World) -> None:
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert any(
        header.startswith("session=") and "Max-Age=0" in header for header in response.headers.get_list("set-cookie")
    )
    assert world.uow.sessions.stored == []


def test_a_token_revoked_at_github_closes_the_session() -> None:
    world = World()

    response = _client(world, FakeInstallationCatalog(error=ProviderTokenRejected("401"))).get(PATH)

    _assert_closed_with_401(response, world)


def test_a_rejected_refresh_closes_the_session() -> None:
    world = World()
    oauth = FakeOAuthProvider()
    oauth.refresh_error = rejecting()
    client = _client(world, oauth=oauth)
    _expired_but_refreshable(world)

    response = client.get(PATH)

    _assert_closed_with_401(response, world)
    assert len(oauth.refreshes) == 1


def test_github_unavailable_on_refresh_is_502_and_keeps_the_session() -> None:
    world = World()
    oauth = FakeOAuthProvider()
    oauth.refresh_error = OAuthProviderUnavailable("GitHub ответил 503 (обновление токена)")
    client = _client(world, oauth=oauth)
    _expired_but_refreshable(world)

    response = client.get(PATH)

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"
    assert len(world.uow.sessions.stored) == 1


def test_a_repeated_list_within_the_ttl_does_not_ask_github() -> None:
    """Кабинет перезапрашивает список на события потока: в пределах TTL — без GitHub (#126)."""
    world = World()
    catalog = FakeInstallationCatalog([API, WEB])
    client = _client(world, catalog)

    first = client.get(PATH)
    second = client.get(PATH, params={"status": "completed"})

    assert first.status_code == second.status_code == 200
    assert catalog.calls.count("list_repositories") == 1
