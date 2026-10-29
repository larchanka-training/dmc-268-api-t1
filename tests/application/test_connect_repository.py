"""Подключение репозитория: use cases на фейках каталога, провайдера входа и unit of work."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.use_cases.connect_repository import (
    ConnectRefusal,
    ListedRepository,
    UserContext,
    connect_installation,
    connect_repository,
    list_with_connections,
)
from app.application.use_cases.user_token import SessionClosed
from app.domain.entities import (
    InstallationRepository,
    Repository,
    RepositoryPermissions,
    Secret,
)
from app.domain.enums import Provider, RepositorySelection
from app.domain.provider_token import ProviderTokenRejected, token_from_grant
from app.domain.session import open_session
from app.domain.vcs_errors import VcsError

from ..fakes.installation_catalog import FakeInstallationCatalog
from ..fakes.oauth import EXPIRING_GRANT, FakeOAuthProvider
from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)
NEW_ID = UUID("01920000-0000-7000-8000-000000000041")
SESSION_ID = UUID(int=0x5E)
READ = RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)
ADMIN = RepositoryPermissions(admin=True, maintain=False, push=False, triage=False)
MAINTAIN = RepositoryPermissions(admin=False, maintain=True, push=False, triage=False)
WRITE = RepositoryPermissions(admin=False, maintain=False, push=True, triage=False)
TRIAGE = RepositoryPermissions(admin=False, maintain=False, push=False, triage=True)


def _repo(provider_id: str, name: str, permissions: RepositoryPermissions) -> InstallationRepository:
    return InstallationRepository(provider_id=provider_id, full_name=f"octo-org/{name}", default_branch="main", permissions=permissions)


API = _repo("101", "api", ADMIN)
WEB = _repo("202", "web", READ)
DOCS = _repo("303", "docs", WRITE)


def _signed_in() -> FakeUnitOfWork:
    """Сессия с токеном, выданным при входе в `NOW`: обновлять не нужно."""
    uow = FakeUnitOfWork()
    uow.sessions.add(open_session(SESSION_ID, UUID(int=1), "cookie", NOW))
    uow.provider_tokens.save(SESSION_ID, token_from_grant(EXPIRING_GRANT, NOW))
    return uow


def _user(oauth: FakeOAuthProvider | None = None) -> UserContext:
    return UserContext(oauth=oauth or FakeOAuthProvider(), session_id=SESSION_ID)


def _registered(provider_id: str) -> Repository:
    return Repository(
        id=UUID(int=1),
        provider=Provider.GITHUB,
        provider_id=provider_id,
        full_name="octo-org/api",
        default_branch="main",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
        connected_at=NOW,
    )


def _connect(
    catalog: FakeInstallationCatalog, uow: FakeUnitOfWork, provider_id: str = "101", provider: Provider = Provider.GITHUB
) -> Repository | ConnectRefusal:
    return connect_repository(catalog, uow, _user(), provider, provider_id, NEW_ID, NOW)


def test_the_list_comes_from_the_user_token_and_carries_the_connection() -> None:
    uow = _signed_in()
    registered = _registered("101")
    uow.repositories.add(registered)
    catalog = FakeInstallationCatalog([WEB, API])

    listed = list_with_connections(catalog, uow, _user(), NOW)

    assert listed == [
        ListedRepository(API, record=registered, can_connect=True),
        ListedRepository(WEB, record=None, can_connect=False),
    ]
    assert catalog.tokens == [Secret("ghu_login_access")]


def test_the_list_is_ordered_by_name_ignoring_case() -> None:
    """Только по имени: при равенстве без учёта регистра — порядок ответа GitHub."""
    upper = _repo("909", "API", ADMIN)
    catalog = FakeInstallationCatalog([WEB, upper, DOCS, API])

    listed = list_with_connections(catalog, _signed_in(), _user(), NOW)

    assert [item.repository for item in listed] == [upper, API, DOCS, WEB]


def test_nothing_visible_is_an_empty_list() -> None:
    assert list_with_connections(FakeInstallationCatalog([]), _signed_in(), _user(), NOW) == []


def test_a_github_failure_passes_up_and_keeps_the_session() -> None:
    uow = _signed_in()
    with pytest.raises(VcsError):
        list_with_connections(FakeInstallationCatalog(error=VcsError("HTTP 503")), uow, _user(), NOW)
    assert [s.id for s in uow.sessions.stored] == [SESSION_ID]


def test_a_rejected_token_closes_the_session() -> None:
    uow = _signed_in()
    catalog = FakeInstallationCatalog(error=ProviderTokenRejected("401"))

    with pytest.raises(SessionClosed):
        list_with_connections(catalog, uow, _user(), NOW)

    assert uow.sessions.stored == []
    assert uow.provider_tokens.stored == {}


def test_admin_connects_and_the_repository_is_recorded() -> None:
    uow = _signed_in()

    outcome = _connect(FakeInstallationCatalog([API, WEB]), uow)

    expected = Repository(
        id=NEW_ID,
        provider=Provider.GITHUB,
        provider_id="101",
        full_name="octo-org/api",
        default_branch="main",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
        connected_at=NOW,
    )
    assert outcome == expected
    assert uow.repositories.get(NEW_ID) == expected


def test_maintain_connects() -> None:
    uow = _signed_in()
    outcome = _connect(FakeInstallationCatalog([_repo("101", "api", MAINTAIN)]), uow)
    assert isinstance(outcome, Repository)


@pytest.mark.parametrize("permissions", [WRITE, TRIAGE])
def test_without_admin_or_maintain_it_is_refused_and_nothing_is_written(permissions: RepositoryPermissions) -> None:
    uow = _signed_in()
    assert _connect(FakeInstallationCatalog([_repo("303", "docs", permissions)]), uow, "303") is ConnectRefusal.MANAGE_REQUIRED
    assert uow.repositories.stored == {}


def test_without_rights_the_user_does_not_learn_it_is_connected() -> None:
    uow = _signed_in()
    uow.repositories.add(_registered("303"))
    assert _connect(FakeInstallationCatalog([DOCS]), uow, "303") is ConnectRefusal.MANAGE_REQUIRED


@pytest.mark.parametrize("installed", [True, False])
def test_a_repository_with_only_pull_is_invisible_whether_or_not_the_app_is_installed(installed: bool) -> None:
    """Чужой публичный репозиторий GitHub отдаёт по id любому, с одним `pull`: это не
    доступ. Отказ — как у невидимого, установка не запрашивается, оракула установки нет."""
    catalog = FakeInstallationCatalog([WEB], not_installed=set() if installed else {WEB.full_name})
    assert _connect(catalog, _signed_in(), "202") is ConnectRefusal.NOT_IN_INSTALLATION
    assert catalog.calls == ["user_repository"]


def test_a_private_repository_with_only_read_needs_manage_rights() -> None:
    """Приватный GitHub отдаёт только участнику: роль read — это доступ, отказ `403`."""
    uow = _signed_in()
    catalog = FakeInstallationCatalog([replace(WEB, private=True)])
    assert _connect(catalog, uow, "202") is ConnectRefusal.MANAGE_REQUIRED
    assert catalog.calls == ["user_repository", "app_installed"]
    assert uow.repositories.stored == {}


def test_a_private_repository_with_only_read_and_no_app_is_not_in_installation() -> None:
    private = replace(WEB, private=True)
    catalog = FakeInstallationCatalog([private], not_installed={private.full_name})
    assert _connect(catalog, _signed_in(), "202") is ConnectRefusal.NOT_IN_INSTALLATION


def test_invisible_and_missing_repositories_get_the_same_refusal() -> None:
    """GitHub не отдаёт пользователю репозиторий, к которому у него нет доступа: для
    use case он неотличим от репозитория, которого нет в установках вовсе."""
    uow = _signed_in()
    catalog = FakeInstallationCatalog([WEB])
    invisible = _connect(catalog, uow, "101")
    missing = _connect(catalog, uow, "999")
    assert invisible is missing is ConnectRefusal.NOT_IN_INSTALLATION


def test_another_provider_is_refused_without_asking_github() -> None:
    catalog = FakeInstallationCatalog([API])
    assert _connect(catalog, _signed_in(), provider=Provider.GITLAB) is ConnectRefusal.NOT_IN_INSTALLATION
    assert catalog.tokens == []


def test_reconnecting_with_rights_is_a_conflict() -> None:
    uow = _signed_in()
    uow.repositories.add(_registered("101"))
    assert _connect(FakeInstallationCatalog([API]), uow) is ConnectRefusal.ALREADY_CONNECTED


def test_the_id_from_githubs_answer_is_recorded() -> None:
    # GitHub отдаёт на `/repositories/0101` тот же репозиторий, что на `/repositories/101`.
    uow = _signed_in()

    outcome = _connect(FakeInstallationCatalog([API]), uow, "0101")

    assert isinstance(outcome, Repository)
    assert outcome.provider_id == "101"
    assert uow.repositories.get(NEW_ID) == outcome


def test_a_connected_repository_under_another_spelling_of_its_id_is_a_conflict() -> None:
    uow = _signed_in()
    uow.repositories.add(_registered("101"))

    assert _connect(FakeInstallationCatalog([API]), uow, "0101") is ConnectRefusal.ALREADY_CONNECTED


def test_a_parallel_connect_that_won_is_a_conflict() -> None:
    uow = _signed_in()
    original_add = uow.repositories.add

    def lose_the_race(repository: Repository) -> bool:
        original_add(_registered(repository.provider_id))
        return False

    uow.repositories.add = lose_the_race  # type: ignore[method-assign]
    assert _connect(FakeInstallationCatalog([API]), uow) is ConnectRefusal.ALREADY_CONNECTED


def test_a_github_failure_on_connect_writes_nothing() -> None:
    uow = _signed_in()
    with pytest.raises(VcsError):
        _connect(FakeInstallationCatalog(error=VcsError("HTTP 503")), uow)
    assert uow.repositories.stored == {}


def test_connect_asks_github_exactly_twice() -> None:
    """Репозиторий по id токеном пользователя, затем установка приложения — без обхода списка."""
    catalog = FakeInstallationCatalog([API])
    assert isinstance(_connect(catalog, _signed_in()), Repository)
    assert catalog.calls == ["user_repository", "app_installed"]


def test_a_visible_repository_without_the_app_is_refused_like_an_invisible_one() -> None:
    uow = _signed_in()
    not_installed = _connect(FakeInstallationCatalog([API], not_installed={"octo-org/api"}), uow)
    invisible = _connect(FakeInstallationCatalog([WEB]), uow)
    assert not_installed is invisible is ConnectRefusal.NOT_IN_INSTALLATION
    assert uow.repositories.stored == {}


def test_the_installation_is_not_checked_for_a_repository_the_user_cannot_see() -> None:
    """Порядок: сначала доступ пользователя — на чужой репозиторий ответ не зависит от установки."""
    catalog = FakeInstallationCatalog([WEB])
    _connect(catalog, _signed_in(), "101")
    assert catalog.calls == ["user_repository"]


def test_without_rights_on_an_installed_repository_it_is_403() -> None:
    assert _connect(FakeInstallationCatalog([DOCS]), _signed_in(), "303") is ConnectRefusal.MANAGE_REQUIRED


def test_setup_counts_only_rows_it_actually_inserted() -> None:
    """Параллельный POST /repositories успел вставить запись между чтением и вставкой Setup URL:
    вставка не перетирает её и в «подключено N» не попадает."""
    uow = _signed_in()
    uow.repositories.list_by_provider_ids = lambda provider, provider_ids: []  # type: ignore[method-assign]
    uow.repositories.add = lambda repository: False  # type: ignore[method-assign]
    catalog = FakeInstallationCatalog([API], installations={7: (RepositorySelection.SELECTED, [API])})

    assert connect_installation(catalog, uow, _user(), 7, lambda: NEW_ID, NOW, connect_selected=True) == 0
