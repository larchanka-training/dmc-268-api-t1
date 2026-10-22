"""Подключение репозитория: use cases на фейках порта и unit of work."""

from datetime import UTC, datetime
from uuid import UUID

from app.application.use_cases.connect_repository import (
    AvailableRepository,
    ConnectRefusal,
    connect_repository,
    list_available_repositories,
)
from app.domain.entities import InstallationRepository, Repository
from app.domain.enums import Provider

from ..fakes.installation_catalog import FakeInstallationCatalog
from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)
NEW_ID = UUID("01920000-0000-7000-8000-000000000041")
API = InstallationRepository(provider_id="101", full_name="octo-org/api", default_branch="main", installation_id=7)
WEB = InstallationRepository(provider_id="202", full_name="octo-org/web", default_branch="trunk", installation_id=8)


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

    available = list_available_repositories(FakeInstallationCatalog([API, WEB]), uow)

    assert available == [AvailableRepository(API, already_connected=True), AvailableRepository(WEB, already_connected=False)]


def test_admin_connects_a_repository_of_the_installation() -> None:
    uow = FakeUnitOfWork()
    catalog = FakeInstallationCatalog([API, WEB], admins={("octo-org/web", "octocat")})

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
    catalog = FakeInstallationCatalog([API], admins={("octo-org/api", "octocat")})

    assert _connect(catalog, uow, "999") is ConnectRefusal.NOT_IN_INSTALLATION
    assert catalog.permission_checks == []
    assert uow.repositories.stored == {}


def test_a_provider_other_than_github_is_not_found() -> None:
    catalog = FakeInstallationCatalog([API], admins={("octo-org/api", "octocat")})

    assert _connect(catalog, FakeUnitOfWork(), provider=Provider.GITLAB) is ConnectRefusal.NOT_IN_INSTALLATION


def test_an_already_connected_repository_is_a_conflict_before_the_permission_check() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(_registered("101"))
    catalog = FakeInstallationCatalog([API], admins={("octo-org/api", "octocat")})

    assert _connect(catalog, uow) is ConnectRefusal.ALREADY_CONNECTED
    assert catalog.permission_checks == []


def test_a_user_without_admin_rights_is_refused() -> None:
    uow = FakeUnitOfWork()
    catalog = FakeInstallationCatalog([API])

    assert _connect(catalog, uow) is ConnectRefusal.ADMIN_REQUIRED
    assert catalog.permission_checks == [("octo-org/api", "octocat")]
    assert uow.repositories.stored == {}
    assert uow.committed == 0


def test_losing_the_race_to_a_parallel_connect_is_a_conflict() -> None:
    """Параллельный запрос записал репозиторий между проверкой и вставкой."""
    uow = FakeUnitOfWork()

    class RacingCatalog(FakeInstallationCatalog):
        def is_admin(self, repository: InstallationRepository, login: str) -> bool:
            uow.repositories.add(_registered(repository.provider_id))
            return True

    assert _connect(RacingCatalog([API]), uow) is ConnectRefusal.ALREADY_CONNECTED
    assert uow.committed == 0
