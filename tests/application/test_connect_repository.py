"""Подключение репозитория: use cases на фейках порта и unit of work."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.use_cases.connect_repository import (
    AvailableRepository,
    ConnectRefusal,
    connect_repository,
    list_available_repositories,
)
from app.domain.entities import InstallationRepository, Repository
from app.domain.enums import Provider, RepositoryRole
from app.domain.vcs_errors import VcsError

from ..fakes.installation_catalog import FakeInstallationCatalog
from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)
NEW_ID = UUID("01920000-0000-7000-8000-000000000041")
API = InstallationRepository(provider_id="101", full_name="octo-org/api", default_branch="main", installation_id=7, private=True)
WEB = InstallationRepository(provider_id="202", full_name="octo-org/web", default_branch="trunk", installation_id=8, private=True)


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
    )


def _connect(
    catalog: FakeInstallationCatalog, uow: FakeUnitOfWork, provider_id: str = "101", provider: Provider = Provider.GITHUB
) -> Repository | ConnectRefusal:
    return connect_repository(catalog, uow, provider, provider_id, "octocat", NEW_ID, NOW)


def test_available_repositories_are_marked_when_already_connected() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(_registered("101"))
    catalog = FakeInstallationCatalog([API, WEB], managers={("octo-org/api", "octocat"), ("octo-org/web", "octocat")})

    available = list_available_repositories(catalog, uow, "octocat")

    assert available == [AvailableRepository(API, already_connected=True), AvailableRepository(WEB, already_connected=False)]


def test_repositories_without_access_are_not_listed() -> None:
    """Видит любой доступ, в том числе read; без доступа (`None`) репозитория в списке нет."""
    catalog = FakeInstallationCatalog([API, WEB], roles={("octo-org/web", "octocat"): RepositoryRole.READ})

    available = list_available_repositories(catalog, FakeUnitOfWork(), "octocat")

    assert available == [AvailableRepository(WEB, already_connected=False)]
    assert catalog.permission_checks == [("octo-org/api", "octocat"), ("octo-org/web", "octocat")]


def test_the_list_is_empty_without_access_anywhere() -> None:
    catalog = FakeInstallationCatalog([API, WEB], managers={("octo-org/web", "someone-else")})

    assert list_available_repositories(catalog, FakeUnitOfWork(), "octocat") == []


def test_a_failing_permission_check_fails_the_list() -> None:
    """Частичный список выдал бы сбой GitHub за отсутствие прав."""
    catalog = FakeInstallationCatalog([API], permission_error=VcsError("GitHub API: HTTP 503"))

    with pytest.raises(VcsError):
        list_available_repositories(catalog, FakeUnitOfWork(), "octocat")


def test_a_manager_connects_a_repository_of_the_installation() -> None:
    uow = FakeUnitOfWork()
    catalog = FakeInstallationCatalog([API, WEB], managers={("octo-org/web", "octocat")})

    outcome = _connect(catalog, uow, "202")

    expected = Repository(
        id=NEW_ID,
        provider=Provider.GITHUB,
        provider_id="202",
        full_name="octo-org/web",
        default_branch="trunk",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )
    assert outcome == expected
    assert uow.repositories.find_by_provider(Provider.GITHUB, "202") == expected
    assert uow.committed == 1


def test_a_repository_outside_the_installation_is_not_found() -> None:
    uow = FakeUnitOfWork()
    catalog = FakeInstallationCatalog([API], managers={("octo-org/api", "octocat")})

    assert _connect(catalog, uow, "999") is ConnectRefusal.NOT_IN_INSTALLATION
    assert catalog.permission_checks == []
    assert uow.repositories.stored == {}


def test_a_provider_other_than_github_is_not_found() -> None:
    catalog = FakeInstallationCatalog([API], managers={("octo-org/api", "octocat")})

    assert _connect(catalog, FakeUnitOfWork(), provider=Provider.GITLAB) is ConnectRefusal.NOT_IN_INSTALLATION


def test_a_manager_connecting_an_already_connected_repository_is_a_conflict() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(_registered("101"))
    catalog = FakeInstallationCatalog([API], managers={("octo-org/api", "octocat")})

    assert _connect(catalog, uow) is ConnectRefusal.ALREADY_CONNECTED


def test_a_non_manager_does_not_learn_that_a_repository_is_connected() -> None:
    """Права проверяются раньше «уже подключён»: без прав — отказ по правам, а не конфликт."""
    uow = FakeUnitOfWork()
    uow.repositories.add(_registered("101"))

    assert _connect(FakeInstallationCatalog([API]), uow) is ConnectRefusal.MANAGE_REQUIRED


@pytest.mark.parametrize("role", [RepositoryRole.ADMIN, RepositoryRole.MAINTAIN])
def test_admin_and_maintain_connect(role: RepositoryRole) -> None:
    catalog = FakeInstallationCatalog([API], roles={("octo-org/api", "octocat"): role})

    assert isinstance(_connect(catalog, FakeUnitOfWork()), Repository)


@pytest.mark.parametrize("role", [RepositoryRole.WRITE, RepositoryRole.TRIAGE, RepositoryRole.READ, RepositoryRole.CUSTOM])
def test_a_role_below_maintain_is_refused(role: RepositoryRole) -> None:
    uow = FakeUnitOfWork()
    catalog = FakeInstallationCatalog([API], roles={("octo-org/api", "octocat"): role})

    assert _connect(catalog, uow) is ConnectRefusal.MANAGE_REQUIRED
    assert uow.repositories.stored == {}


def test_a_user_without_manage_rights_is_refused() -> None:
    uow = FakeUnitOfWork()
    catalog = FakeInstallationCatalog([API])

    assert _connect(catalog, uow) is ConnectRefusal.MANAGE_REQUIRED
    assert catalog.permission_checks == [("octo-org/api", "octocat")]
    assert uow.repositories.stored == {}
    assert uow.committed == 0


def test_losing_the_race_to_a_parallel_connect_is_a_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Параллельный запрос записал репозиторий между проверкой и вставкой: `add` отвечает `False`."""
    uow = FakeUnitOfWork()
    # Проверка «уже подключён» прошла до записи параллельного запроса.
    monkeypatch.setattr(uow.repositories, "find_by_provider", lambda provider, provider_id: None)
    # Вставку отклоняет уникальность пары — как `ON CONFLICT DO NOTHING` адаптера.
    monkeypatch.setattr(uow.repositories, "add", lambda repository: False)
    catalog = FakeInstallationCatalog([API], managers={("octo-org/api", "octocat")})

    assert _connect(catalog, uow) is ConnectRefusal.ALREADY_CONNECTED
    assert uow.committed == 0
