"""Контракт `/api/v1/repositories`. Без БД — фейковые `UnitOfWork`, каталог и провайдер входа.

Сессия — настоящая проверка cookie (`current_session`) над фейковой базой: так
видно, что непригодный токен закрывает сессию и удаляет cookie.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_installation_catalog,
    get_token_provider,
    get_unit_of_work,
    get_unit_of_work_factory,
)
from app.api.factory import create_app
from app.api.pagination import MAX_LIMIT
from app.api.session import SESSION_COOKIE
from app.config import Settings
from app.domain.entities import (
    InstallationRepository,
    ProviderToken,
    Repository,
    RepositoryPermissions,
)
from app.domain.enums import Provider
from app.domain.ids import new_id
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_token import ProviderTokenRejected, token_from_grant
from app.domain.session import open_session
from app.domain.vcs_errors import VcsError

from .fakes.auth import SIGNED_IN
from .fakes.installation_catalog import FakeInstallationCatalog
from .fakes.oauth import EXPIRING_GRANT, FakeOAuthProvider, rejecting
from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)
SESSION_ID = UUID(int=0x5E)
COOKIE = "cookie-of-the-signed-in-user"


def _repository(n: int) -> Repository:
    return Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id=str(n),
        full_name=f"owner/repo-{n}",
        default_branch="main",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )


ORIGIN = "https://review.example"
ADMIN = RepositoryPermissions(admin=True, maintain=False, push=False, triage=False)
MAINTAIN = RepositoryPermissions(admin=False, maintain=True, push=False, triage=False)
READ = RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)
WRITE = RepositoryPermissions(admin=False, maintain=False, push=True, triage=False)
TRIAGE = RepositoryPermissions(admin=False, maintain=False, push=False, triage=True)
API = InstallationRepository(provider_id="101", full_name="octo-org/api", default_branch="main", permissions=ADMIN)
WEB = InstallationRepository(provider_id="202", full_name="octo-org/web", default_branch="trunk", permissions=READ)
WRITER = InstallationRepository(provider_id="303", full_name="octo-org/docs", default_branch="main", permissions=WRITE)


def _seen(*repositories: Repository) -> FakeInstallationCatalog:
    """GitHub отдаёт пользователю эти зарегистрированные репозитории — они видны в кабинете."""
    return FakeInstallationCatalog(
        [InstallationRepository(r.provider_id, r.full_name, r.default_branch, READ) for r in repositories]
    )


def _client(
    uow: FakeUnitOfWork,
    catalog: FakeInstallationCatalog | None = None,
    *,
    signed_in: bool = True,
    with_token: bool = True,
    oauth: FakeOAuthProvider | None = None,
    app_slug: str = "",
) -> TestClient:
    """Клиент с живой сессией в `uow` и токеном, выданным при входе только что."""
    app = create_app(SETTINGS.model_copy(update={"public_origin": ORIGIN, "github_app_slug": app_slug}))
    now = datetime.now(UTC)
    uow.users.add(SIGNED_IN)
    uow.sessions.add(open_session(SESSION_ID, SIGNED_IN.id, COOKIE, now))
    if with_token:
        uow.provider_tokens.save(SESSION_ID, token_from_grant(EXPIRING_GRANT, now))

    def override_uow() -> Iterator[FakeUnitOfWork]:
        yield uow

    app.dependency_overrides[get_unit_of_work] = override_uow
    app.dependency_overrides[get_unit_of_work_factory] = lambda: lambda: uow
    app.dependency_overrides[get_installation_catalog] = lambda: catalog or FakeInstallationCatalog()
    app.dependency_overrides[get_token_provider] = lambda: oauth or FakeOAuthProvider()
    client = TestClient(app, base_url=ORIGIN)
    if signed_in:
        client.cookies.set(SESSION_COOKIE, COOKIE)
    return client


def _connect(client: TestClient, provider_id: str = "101", provider: str = "github") -> httpx2.Response:
    response: httpx2.Response = client.post(
        "/api/v1/repositories", json={"provider": provider, "provider_id": provider_id}, headers={"Origin": ORIGIN}
    )
    return response


def _session_cookie_cleared(response: httpx2.Response) -> bool:
    return any(
        header.startswith(f"{SESSION_COOKIE}=") and "Max-Age=0" in header
        for header in response.headers.get_list("set-cookie")
    )


def test_returns_registered_repositories() -> None:
    uow = FakeUnitOfWork()
    first, second = _repository(1), _repository(2)
    uow.repositories.add(first)
    uow.repositories.add(second)
    client = _client(uow, _seen(first, second))

    response = client.get("/api/v1/repositories")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert {item["full_name"] for item in body["items"]} == {"owner/repo-1", "owner/repo-2"}


def test_one_repository_by_id_is_the_one_behind_its_list_row() -> None:
    uow = FakeUnitOfWork()
    repository = _repository(1)
    uow.repositories.add(repository)
    client = _client(uow, _seen(repository))

    one = client.get(f"/api/v1/repositories/{repository.id}")
    (row,) = client.get("/api/v1/repositories").json()["items"]

    assert one.status_code == 200
    assert one.json()["id"] == row["id"] == str(repository.id)
    assert one.json()["full_name"] == row["full_name"]


def test_a_repository_beyond_the_first_page_is_found_by_id() -> None:
    """Страница настроек не ищет репозиторий в первых 200 строках списка."""
    uow = FakeUnitOfWork()
    for n in range(MAX_LIMIT):
        uow.repositories.add(_repository(n))
    last = _repository(MAX_LIMIT)
    uow.repositories.add(last)

    response = _client(uow, _seen(last)).get(f"/api/v1/repositories/{last.id}")

    assert response.status_code == 200
    assert response.json()["full_name"] == f"owner/repo-{MAX_LIMIT}"


def test_an_unknown_repository_id_is_404() -> None:
    repository_id = new_id()

    response = _client(FakeUnitOfWork()).get(f"/api/v1/repositories/{repository_id}")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "REPOSITORY_NOT_FOUND"


def test_a_repository_id_that_is_not_a_uuid_is_a_validation_error() -> None:
    response = _client(FakeUnitOfWork()).get("/api/v1/repositories/repo-1")

    assert response.status_code == 422
    assert response.json()["errors"] == [{"where": "path.repository_id", "reason": "ожидается UUID"}]


def test_one_repository_requires_a_session() -> None:
    response = _client(FakeUnitOfWork(), signed_in=False).get(f"/api/v1/repositories/{new_id()}")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_limit_above_the_ceiling_is_capped_not_rejected() -> None:
    uow = FakeUnitOfWork()
    repositories = [_repository(n) for n in range(MAX_LIMIT + 5)]
    for repository in repositories:
        uow.repositories.add(repository)
    client = _client(uow, _seen(*repositories))

    response = client.get("/api/v1/repositories", params={"limit": 10_000})

    assert response.status_code == 200
    assert len(response.json()["items"]) == MAX_LIMIT


# --- видимость: то же правило, что у прогонов, списка прогонов и потока (#129) -----------


def test_the_list_holds_only_repositories_seen_on_github() -> None:
    """Зарегистрированный репозиторий, которого GitHub пользователю не отдаёт, в списке не виден."""
    uow = FakeUnitOfWork()
    seen, hidden = _repository(1), _repository(2)
    gitlab = replace(_repository(3), provider=Provider.GITLAB, provider_id=seen.provider_id)
    for repository in (seen, hidden, gitlab):
        uow.repositories.add(repository)

    response = _client(uow, _seen(seen)).get("/api/v1/repositories")

    assert [item["id"] for item in response.json()["items"]] == [str(seen.id)]


def test_the_list_is_paged_by_name_ignoring_case() -> None:
    """Порядок — `full_name` без учёта регистра, затем `provider_id`; подключённые и нет вперемешку."""
    names = ["octo/delta", "Octo/Alpha", "octo/charlie", "octo/bravo", "octo/echo"]
    seen = [InstallationRepository(str(100 + n), name, "main", ADMIN) for n, name in enumerate(names)]
    uow = FakeUnitOfWork()
    uow.repositories.add(replace(_repository(102), full_name="octo/charlie"))
    client = _client(uow, FakeInstallationCatalog(seen))

    whole = client.get("/api/v1/repositories").json()["items"]
    page = client.get("/api/v1/repositories", params={"limit": 2, "offset": 2}).json()["items"]

    assert [item["full_name"] for item in whole] == ["Octo/Alpha", "octo/bravo", "octo/charlie", "octo/delta", "octo/echo"]
    assert [item["full_name"] for item in page] == ["octo/charlie", "octo/delta"]
    assert [item["connected"] for item in page] == [True, False]


def test_a_repository_hidden_on_github_is_404_like_a_missing_one() -> None:
    uow = FakeUnitOfWork()
    hidden = _repository(1)
    uow.repositories.add(hidden)
    client = _client(uow, FakeInstallationCatalog([WEB]))

    response = client.get(f"/api/v1/repositories/{hidden.id}")
    missing = client.get(f"/api/v1/repositories/{new_id()}")

    assert response.status_code == missing.status_code == 404
    assert response.json()["code"] == "REPOSITORY_NOT_FOUND"
    assert response.json()["detail"] == missing.json()["detail"]


def test_a_repository_outside_github_is_visible_to_nobody() -> None:
    uow = FakeUnitOfWork()
    gitlab = replace(_repository(1), provider=Provider.GITLAB)
    uow.repositories.add(gitlab)

    response = _client(uow, _seen(_repository(1))).get(f"/api/v1/repositories/{gitlab.id}")

    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/repositories", f"/api/v1/repositories/{new_id()}"])
def test_github_failure_on_registered_repositories_is_502(path: str) -> None:
    response = _client(FakeUnitOfWork(), FakeInstallationCatalog(error=VcsError("GitHub API: HTTP 503"))).get(path)

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"


@pytest.mark.parametrize("path", ["/api/v1/repositories", f"/api/v1/repositories/{new_id()}"])
def test_a_revoked_token_on_registered_repositories_closes_the_session(path: str) -> None:
    uow = FakeUnitOfWork()

    response = _client(uow, FakeInstallationCatalog(error=ProviderTokenRejected("401"))).get(path)

    assert response.status_code == 401
    assert _session_cookie_cleared(response)
    assert uow.sessions.stored == []


def test_an_invalid_limit_does_not_ask_github() -> None:
    catalog = _seen(_repository(1))

    response = _client(FakeUnitOfWork(), catalog).get("/api/v1/repositories", params={"limit": 0})

    assert response.status_code == 422
    assert catalog.calls == []


def test_a_non_integer_limit_is_a_validation_error_without_the_input() -> None:
    response = _client(FakeUnitOfWork()).get("/api/v1/repositories", params={"limit": "<b>qzq</b>"})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"] == [{"where": "query.limit", "reason": "ожидается целое число"}]
    # Маркер не из шестнадцатеричных символов: trace_id в ответе случайный hex и иногда
    # содержал «abc», тест падал без причины.
    assert "qzq" not in response.text
    assert "<b>" not in response.text


def test_a_limit_below_one_is_a_validation_error() -> None:
    response = _client(FakeUnitOfWork()).get("/api/v1/repositories", params={"limit": 0})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"] == [{"where": "query.limit", "reason": "должно быть не меньше 1"}]


def test_the_list_holds_connected_and_unconnected_repositories_together() -> None:
    """Один список: всё, что GitHub отдал по токену, у подключённых — `id` и `connected_at`."""
    uow = FakeUnitOfWork()
    registered = replace(_repository(101), full_name="octo-org/old-name", default_branch="master")
    uow.repositories.add(registered)
    catalog = FakeInstallationCatalog([WEB, API])
    client = _client(uow, catalog, app_slug="dmc-268-review")

    response = client.get("/api/v1/repositories")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "provider": "github",
                "provider_id": "101",
                # Имя и ветка — из ответа GitHub, а не из записи: репозиторий могли переименовать.
                "full_name": "octo-org/api",
                "default_branch": "main",
                "connected": True,
                "id": str(registered.id),
                "can_connect": True,
                "connected_at": "2026-09-29T00:00:00Z",
            },
            {
                "provider": "github",
                "provider_id": "202",
                "full_name": "octo-org/web",
                "default_branch": "trunk",
                "connected": False,
                "id": None,
                "can_connect": False,
                "connected_at": None,
            },
        ],
        "install_url": "https://github.com/apps/dmc-268-review/installations/new",
    }
    assert [token.reveal() for token in catalog.tokens] == ["ghu_login_access"]


def test_without_an_app_slug_the_install_url_is_null() -> None:
    response = _client(FakeUnitOfWork(), FakeInstallationCatalog([API])).get("/api/v1/repositories")

    assert response.status_code == 200
    assert response.json()["install_url"] is None


def test_a_registered_repository_github_does_not_show_is_not_listed_as_connected() -> None:
    """Подключённый, которого GitHub пользователю не отдаёт, в списке не появляется вовсе."""
    uow = FakeUnitOfWork()
    uow.repositories.add(_repository(101))

    items = _client(uow, FakeInstallationCatalog([WEB])).get("/api/v1/repositories").json()["items"]

    assert [(item["full_name"], item["connected"]) for item in items] == [("octo-org/web", False)]


def test_the_connect_list_endpoint_is_gone() -> None:
    """`/available` удалена: путь разбирается как id репозитория, к GitHub запроса нет."""
    catalog = FakeInstallationCatalog([API])

    response = _client(FakeUnitOfWork(), catalog).get("/api/v1/repositories/available")

    assert response.status_code == 422
    assert response.json()["errors"] == [{"where": "path.repository_id", "reason": "ожидается UUID"}]
    assert catalog.calls == []
    assert "/api/v1/repositories/available" not in create_app(SETTINGS).openapi()["paths"]


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("permissions", [ADMIN, MAINTAIN, WRITE, TRIAGE, READ])
def test_can_connect_in_the_list_matches_what_connecting_answers(
    permissions: RepositoryPermissions, private: bool, connected: bool
) -> None:
    """`can_connect` в списке — то же решение, что принимает `POST /repositories` (ui#45):
    `true` — подключение пройдёт (`201`) или репозиторий уже подключён (`409`), `false` — отказ."""
    uow = FakeUnitOfWork()
    if connected:
        uow.repositories.add(_repository(101))
    client = _client(uow, FakeInstallationCatalog([replace(API, permissions=permissions, private=private)]))

    (item,) = client.get("/api/v1/repositories").json()["items"]
    response = _connect(client, API.provider_id)

    assert item["can_connect"] is (response.status_code in {201, 409}), response.json()


def test_the_user_token_never_reaches_the_response() -> None:
    response = _client(FakeUnitOfWork(), FakeInstallationCatalog([API])).get("/api/v1/repositories")

    assert response.status_code == 200
    for value in ("ghu_login_access", "ghr_login_refresh"):
        assert value not in response.text
        assert value not in str(response.headers)


def test_a_read_only_user_sees_the_repository_but_cannot_connect_it() -> None:
    """Только `pull`: в списке есть (его отдал GitHub), подключение — `404`, как у невидимого."""
    uow = FakeUnitOfWork()
    client = _client(uow, FakeInstallationCatalog([WEB]))

    listed = client.get("/api/v1/repositories")
    connected = _connect(client, WEB.provider_id)

    assert listed.status_code == 200
    assert [item["full_name"] for item in listed.json()["items"]] == ["octo-org/web"]
    assert connected.status_code == 404
    assert connected.json()["code"] == "REPOSITORY_NOT_IN_INSTALLATION"
    assert uow.repositories.stored == {}


@pytest.mark.parametrize("installed", [True, False])
def test_a_public_repository_without_explicit_rights_looks_exactly_like_a_missing_one(installed: bool) -> None:
    """`GET /repositories/{id}` отдаёт чужой публичный репозиторий с одним `pull`: ответ
    не зависит от того, установлено ли туда приложение, и совпадает с ответом на отсутствующий."""
    catalog = FakeInstallationCatalog([WEB], not_installed=set() if installed else {WEB.full_name})
    public = _connect(_client(FakeUnitOfWork(), catalog), WEB.provider_id)
    missing = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([])), "999")

    assert public.status_code == missing.status_code == 404
    strip = lambda body: {k: v for k, v in body.items() if k not in {"trace_id", "instance"}}
    assert strip(public.json()) == strip(missing.json())
    assert "app_installed" not in catalog.calls


def test_a_private_repository_with_only_read_is_403() -> None:
    """Приватный репозиторий GitHub отдаёт только участнику: с ролью read отказ —
    `403 REPOSITORY_MANAGE_REQUIRED`, а не `404`."""
    uow = FakeUnitOfWork()
    response = _connect(_client(uow, FakeInstallationCatalog([replace(WEB, private=True)])), WEB.provider_id)

    assert response.status_code == 403
    assert response.json()["code"] == "REPOSITORY_MANAGE_REQUIRED"
    assert uow.repositories.stored == {}


def test_a_maintainer_connects_a_repository() -> None:
    catalog = FakeInstallationCatalog([InstallationRepository("101", "octo-org/api", "main", MAINTAIN)])

    assert _connect(_client(FakeUnitOfWork(), catalog)).status_code == 201


def test_the_list_is_empty_when_github_shows_nothing() -> None:
    response = _client(FakeUnitOfWork(), FakeInstallationCatalog([])).get("/api/v1/repositories")

    assert response.status_code == 200
    assert response.json() == {"items": [], "install_url": None}


def test_the_list_answers_502_when_github_fails_and_keeps_the_session() -> None:
    uow = FakeUnitOfWork()
    client = _client(uow, FakeInstallationCatalog(error=VcsError("GitHub API: HTTP 503")))

    response = client.get("/api/v1/repositories")

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "SCM_UNAVAILABLE"
    assert len(uow.sessions.stored) == 1


def test_the_list_requires_a_session() -> None:
    catalog = FakeInstallationCatalog([API])

    response = _client(FakeUnitOfWork(), catalog, signed_in=False).get("/api/v1/repositories")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert catalog.calls == []


def test_a_session_without_a_stored_token_must_sign_in_again() -> None:
    """Сессия, открытая до хранения токенов: 401, сессия закрыта, cookie удалена."""
    uow = FakeUnitOfWork()
    client = _client(uow, FakeInstallationCatalog([API]), with_token=False)

    for response in (client.get("/api/v1/repositories"), _connect(_client(uow, with_token=False))):
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"
        assert _session_cookie_cleared(response)
    assert uow.sessions.stored == []


def test_a_revoked_token_closes_the_session() -> None:
    uow = FakeUnitOfWork()
    client = _client(uow, FakeInstallationCatalog(error=ProviderTokenRejected("401")))

    response = client.get("/api/v1/repositories")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert _session_cookie_cleared(response)
    assert uow.sessions.stored == []
    # Следующий запрос с той же cookie — уже без сессии.
    client.cookies.set(SESSION_COOKIE, COOKIE)
    assert client.get("/api/v1/auth/me").status_code == 401


def _expired_but_refreshable() -> ProviderToken:
    """Access token выдан давно и истёк, refresh token бессрочный."""
    return replace(token_from_grant(EXPIRING_GRANT, datetime(2026, 1, 1, tzinfo=UTC)), refresh_expires_at=None)


def test_a_failed_refresh_closes_the_session() -> None:
    """Токен истёк, refresh token отвергнут — 401 и закрытая сессия."""
    uow = FakeUnitOfWork()
    oauth = FakeOAuthProvider()
    oauth.refresh_error = rejecting()
    client = _client(uow, FakeInstallationCatalog([API]), oauth=oauth)
    uow.provider_tokens.save(SESSION_ID, _expired_but_refreshable())

    response = _connect(client)

    assert response.status_code == 401
    assert _session_cookie_cleared(response)
    assert uow.sessions.stored == []
    assert uow.repositories.stored == {}
    assert len(oauth.refreshes) == 1


def test_github_unavailable_on_refresh_is_502_and_keeps_the_session() -> None:
    uow = FakeUnitOfWork()
    oauth = FakeOAuthProvider()
    oauth.refresh_error = OAuthProviderUnavailable("GitHub ответил 503 (обновление токена)")
    client = _client(uow, FakeInstallationCatalog([API]), oauth=oauth)
    uow.provider_tokens.save(SESSION_ID, _expired_but_refreshable())

    response = client.get("/api/v1/repositories")

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"
    assert len(uow.sessions.stored) == 1


def test_an_admin_connects_a_repository() -> None:
    uow = FakeUnitOfWork()
    web = InstallationRepository("202", "octo-org/web", "trunk", ADMIN)
    client = _client(uow, FakeInstallationCatalog([API, web]))

    response = _connect(client, "202")

    assert response.status_code == 201
    body = response.json()
    assert body["provider"] == "github"
    assert body["provider_id"] == "202"
    assert body["full_name"] == "octo-org/web"
    assert body["default_branch"] == "trunk"
    assert body["auto_review_enabled"] is True
    assert body["created_at"].endswith("Z")
    stored = uow.repositories.find_by_provider(Provider.GITHUB, "202")
    assert stored is not None
    assert str(stored.id) == body["id"]


def test_connecting_without_manage_rights_is_403() -> None:
    uow = FakeUnitOfWork()
    response = _connect(_client(uow, FakeInstallationCatalog([WRITER])), WRITER.provider_id)

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "REPOSITORY_MANAGE_REQUIRED"
    assert uow.repositories.stored == {}


def test_an_invisible_repository_looks_exactly_like_a_missing_one() -> None:
    """Репозиторий, который GitHub пользователю не отдал, и несуществующий — один ответ."""
    invisible = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([WEB])), "101")
    missing = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([WEB])), "999")

    assert invisible.status_code == missing.status_code == 404
    strip = lambda body: {k: v for k, v in body.items() if k not in {"trace_id", "instance"}}
    assert strip(invisible.json()) == strip(missing.json())
    assert invisible.json()["code"] == "REPOSITORY_NOT_IN_INSTALLATION"


def test_a_repository_without_the_app_looks_exactly_like_an_invisible_one() -> None:
    """Приложение не установлено (или установка приостановлена) — тот же 404, что «не виден»."""
    not_installed = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([API], not_installed={API.full_name})))
    invisible = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([WEB])))

    assert not_installed.status_code == invisible.status_code == 404
    strip = lambda body: {k: v for k, v in body.items() if k != "trace_id"}
    assert strip(not_installed.json()) == strip(invisible.json())


def test_connecting_a_gitlab_repository_is_404() -> None:
    response = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([API])), provider="gitlab")

    assert response.status_code == 404
    assert response.json()["code"] == "REPOSITORY_NOT_IN_INSTALLATION"


@pytest.mark.parametrize("provider_id", ["0101", "+101", "١٠١", "²"])
def test_a_non_canonical_id_is_422_without_asking_github(provider_id: str) -> None:
    catalog = FakeInstallationCatalog([API])

    response = _connect(_client(FakeUnitOfWork(), catalog), provider_id)

    assert response.status_code == 422
    assert response.json()["errors"] == [{"where": "body.provider_id", "reason": "недопустимое значение"}]
    assert catalog.calls == []


def test_connecting_twice_is_409() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(_repository(101))

    response = _connect(_client(uow, FakeInstallationCatalog([API])))

    assert response.status_code == 409
    assert response.json()["code"] == "REPOSITORY_ALREADY_CONNECTED"


def test_connecting_a_connected_repository_without_manage_rights_is_403_not_409() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(_repository(303))

    response = _connect(_client(uow, FakeInstallationCatalog([WRITER])), WRITER.provider_id)

    assert response.status_code == 403
    assert response.json()["code"] == "REPOSITORY_MANAGE_REQUIRED"


def test_connecting_answers_502_when_github_fails() -> None:
    uow = FakeUnitOfWork()
    response = _connect(_client(uow, FakeInstallationCatalog(error=VcsError("GitHub API: HTTP 503"))))

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"
    assert uow.repositories.stored == {}


def test_connecting_requires_a_session() -> None:
    response = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([API]), signed_in=False))

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


# --- кэш видимых репозиториев (#126) ---------------------------------------------


def _github_listings(catalog: FakeInstallationCatalog) -> int:
    return catalog.calls.count("list_repositories")


def test_a_repeated_list_within_the_ttl_does_not_ask_github() -> None:
    catalog = FakeInstallationCatalog([API])
    client = _client(FakeUnitOfWork(), catalog)

    client.get("/api/v1/repositories")
    client.get("/api/v1/repositories")

    assert _github_listings(catalog) == 1


def test_a_connected_repository_is_listed_at_once_without_waiting_for_the_ttl() -> None:
    """Кэшируется только ответ GitHub, подключённые читаются из базы: подключённый виден сразу."""
    catalog = FakeInstallationCatalog([API])
    client = _client(FakeUnitOfWork(), catalog)
    (before,) = client.get("/api/v1/repositories").json()["items"]

    connected = _connect(client)
    (after,) = client.get("/api/v1/repositories").json()["items"]

    assert (before["connected"], before["id"]) == (False, None)
    assert connected.status_code == 201
    assert (after["connected"], after["id"]) == (True, connected.json()["id"])
    assert after["connected_at"] == connected.json()["created_at"]
    assert _github_listings(catalog) == 1
    assert client.get(f"/api/v1/repositories/{after['id']}").status_code == 200


# --- выключение ревью: контракт первым, реализация — следующим шагом #243 -----------


def _disconnect(client: TestClient, repository_id: str, *, origin: str | None = ORIGIN) -> httpx2.Response:
    headers = {"Origin": origin} if origin else {}
    response: httpx2.Response = client.delete(f"/api/v1/repositories/{repository_id}", headers=headers)
    return response


def test_disconnecting_is_planned_and_answers_501() -> None:
    uow = FakeUnitOfWork()
    repository = _repository(101)
    uow.repositories.add(repository)

    response = _disconnect(_client(uow, _seen(repository)), str(repository.id))

    assert response.status_code == 501
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "NOT_IMPLEMENTED"
    assert list(uow.repositories.stored.values()) == [repository]


def test_disconnecting_checks_origin_session_and_id_before_501() -> None:
    uow = FakeUnitOfWork()

    without_origin = _disconnect(_client(uow), str(new_id()), origin=None)
    without_session = _disconnect(_client(uow, signed_in=False), str(new_id()))
    bad_id = _disconnect(_client(uow), "repo-1")

    assert without_origin.status_code == 403
    assert without_origin.json()["code"] == "ORIGIN_FORBIDDEN"
    assert without_session.status_code == 401
    assert bad_id.status_code == 422
    assert bad_id.json()["errors"] == [{"where": "path.repository_id", "reason": "ожидается UUID"}]
