"""Кэш списка видимых репозиториев (#126) и номера установки поверх `InstallationCatalog`. Без сети."""

import logging

import pytest

from app.domain.entities import InstallationRepository, RepositoryPermissions, Secret
from app.domain.vcs_errors import VcsError
from app.infrastructure.cache.memory import InMemoryCacheStore
from app.infrastructure.vcs.cached_installations import CachedInstallationCatalog

from ..fakes.context import FakeCache
from ..fakes.installation_catalog import FakeInstallationCatalog

TTL = 30
INSTALLATION_ID_TTL = 300
TOKEN = Secret("ghu_user-token")
PRIVATE = InstallationRepository(
    "101", "octo-org/api", "main", RepositoryPermissions(admin=True, maintain=False, push=True, triage=False), True
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _cached(catalog: FakeInstallationCatalog, clock: Clock | None = None) -> CachedInstallationCatalog:
    return CachedInstallationCatalog(
        catalog, InMemoryCacheStore(monotonic=clock or Clock()), TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL
    )


def test_a_repeated_list_within_the_ttl_does_not_ask_github() -> None:
    catalog = FakeInstallationCatalog([PRIVATE])
    cached = _cached(catalog)

    first = cached.list_repositories(TOKEN)
    second = cached.list_repositories(TOKEN)

    assert first == second == [PRIVATE]
    assert catalog.calls == ["list_repositories"]


def test_after_the_ttl_github_is_asked_again() -> None:
    catalog = FakeInstallationCatalog([PRIVATE])
    clock = Clock()
    cached = _cached(catalog, clock)

    cached.list_repositories(TOKEN)
    clock.now = TTL
    cached.list_repositories(TOKEN)

    assert catalog.calls == ["list_repositories", "list_repositories"]


def test_another_token_does_not_see_the_entry() -> None:
    """Другой пользователь или обновлённый токен — другой ключ: чужой список не отдаётся."""
    catalog = FakeInstallationCatalog([PRIVATE])
    cached = _cached(catalog)

    cached.list_repositories(TOKEN)
    cached.list_repositories(Secret("ghu_other-token"))

    assert catalog.calls == ["list_repositories", "list_repositories"]


def test_the_token_itself_is_not_stored() -> None:
    cache = FakeCache()
    CachedInstallationCatalog(FakeInstallationCatalog([PRIVATE]), cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL).list_repositories(TOKEN)

    [(key, value, ttl)] = cache.sets
    assert TOKEN.reveal() not in key
    assert TOKEN.reveal() not in value
    assert ttl == TTL


def test_a_github_failure_is_not_cached() -> None:
    failing = FakeInstallationCatalog(error=VcsError("HTTP 503"))
    cache = InMemoryCacheStore()

    with pytest.raises(VcsError):
        CachedInstallationCatalog(failing, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL).list_repositories(TOKEN)
    recovered = FakeInstallationCatalog([PRIVATE])

    assert CachedInstallationCatalog(recovered, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL).list_repositories(TOKEN) == [PRIVATE]
    assert recovered.calls == ["list_repositories"]


def test_a_broken_cache_is_a_miss_not_an_error(caplog: pytest.LogCaptureFixture) -> None:
    catalog = FakeInstallationCatalog([PRIVATE])
    cached = CachedInstallationCatalog(catalog, FakeCache(broken=True), TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL)

    with caplog.at_level(logging.WARNING):
        assert cached.list_repositories(TOKEN) == [PRIVATE]

    assert catalog.calls == ["list_repositories"]
    assert TOKEN.reveal() not in caplog.text


def test_connecting_goes_past_the_cache() -> None:
    """Проверки подключения — всегда к GitHub: только что поставленное приложение видно сразу."""
    catalog = FakeInstallationCatalog([PRIVATE])
    cached = _cached(catalog)

    cached.user_repository(TOKEN, "101")
    cached.user_repository(TOKEN, "101")
    cached.app_installed("octo-org/api")

    assert catalog.calls == ["user_repository", "user_repository", "app_installed"]


def test_a_refreshing_catalog_always_asks_github_and_feeds_the_cache() -> None:
    """Страница подключения: всегда к GitHub, свежий ответ — читающим из кэша."""
    stale = FakeInstallationCatalog([])
    fresh = FakeInstallationCatalog([PRIVATE])
    cache = InMemoryCacheStore()
    reading = CachedInstallationCatalog(stale, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL)
    refreshing = CachedInstallationCatalog(fresh, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL, read=False)

    assert reading.list_repositories(TOKEN) == []
    assert refreshing.list_repositories(TOKEN) == [PRIVATE]
    assert refreshing.list_repositories(TOKEN) == [PRIVATE]

    assert fresh.calls == ["list_repositories", "list_repositories"]
    assert reading.list_repositories(TOKEN) == [PRIVATE]
    assert stale.calls == ["list_repositories"]


# --- Номер установки приложения на репозитории -----------------------------------


def test_a_repeated_installation_id_within_the_ttl_does_not_ask_github() -> None:
    catalog = FakeInstallationCatalog(installation_ids={"octo-org/api": 42})
    cache = FakeCache()
    cached = CachedInstallationCatalog(catalog, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL)

    assert cached.repository_installation_id("octo-org/api") == 42
    assert cached.repository_installation_id("octo-org/api") == 42

    assert catalog.calls == ["repository_installation_id"]
    assert cache.sets == [("installation-id:v1:octo-org/api", "42", INSTALLATION_ID_TTL)]


def test_the_installation_id_expires_after_its_own_ttl() -> None:
    catalog = FakeInstallationCatalog()
    clock = Clock()
    cached = _cached(catalog, clock)

    cached.repository_installation_id("octo-org/api")
    clock.now = TTL
    cached.repository_installation_id("octo-org/api")
    clock.now = INSTALLATION_ID_TTL
    cached.repository_installation_id("octo-org/api")

    assert catalog.calls == ["repository_installation_id", "repository_installation_id"]


def test_not_installed_is_not_cached() -> None:
    """Только что поставленное приложение видно сразу, а не через TTL."""
    catalog = FakeInstallationCatalog(not_installed={"octo-org/api"})
    cached = _cached(catalog)

    assert cached.repository_installation_id("octo-org/api") is None
    assert cached.repository_installation_id("octo-org/api") is None

    assert catalog.calls == ["repository_installation_id", "repository_installation_id"]


def test_a_github_failure_of_the_installation_id_is_not_cached() -> None:
    cache = InMemoryCacheStore()
    failing = FakeInstallationCatalog(error=VcsError("HTTP 503"))

    with pytest.raises(VcsError):
        CachedInstallationCatalog(failing, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL).repository_installation_id("o/r")
    recovered = FakeInstallationCatalog(installation_ids={"o/r": 7})

    cached = CachedInstallationCatalog(recovered, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL)
    assert cached.repository_installation_id("o/r") == 7
    assert recovered.calls == ["repository_installation_id"]


@pytest.mark.parametrize("raw", ["not-a-number", "", "1.5", "[]"])
def test_a_broken_installation_id_entry_is_a_miss(raw: str) -> None:
    catalog = FakeInstallationCatalog(installation_ids={"o/r": 7})
    cache = FakeCache(data={"installation-id:v1:o/r": raw})

    cached = CachedInstallationCatalog(catalog, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL)
    assert cached.repository_installation_id("o/r") == 7

    assert catalog.calls == ["repository_installation_id"]


def test_a_broken_cache_store_is_a_miss_for_the_installation_id() -> None:
    catalog = FakeInstallationCatalog(installation_ids={"o/r": 7})
    cached = CachedInstallationCatalog(catalog, FakeCache(broken=True), TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL)

    assert cached.repository_installation_id("o/r") == 7
    assert cached.repository_installation_id("o/r") == 7

    assert catalog.calls == ["repository_installation_id", "repository_installation_id"]


def test_app_installed_goes_past_the_installation_id_cache() -> None:
    catalog = FakeInstallationCatalog()
    cached = _cached(catalog)

    cached.repository_installation_id("octo-org/api")
    cached.app_installed("octo-org/api")
    cached.app_installed("octo-org/api")

    assert catalog.calls == ["repository_installation_id", "app_installed", "app_installed"]


def test_a_refreshing_catalog_asks_github_for_the_installation_id_and_feeds_the_cache() -> None:
    """Обновляющий каталог спрашивает GitHub номер установки и пишет его в кэш."""
    stale = FakeInstallationCatalog(installation_ids={"o/r": 7})
    fresh = FakeInstallationCatalog(installation_ids={"o/r": 8})
    cache = InMemoryCacheStore()
    reading = CachedInstallationCatalog(stale, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL)
    refreshing = CachedInstallationCatalog(fresh, cache, TTL, installation_id_ttl_seconds=INSTALLATION_ID_TTL, read=False)

    assert reading.repository_installation_id("o/r") == 7
    assert refreshing.repository_installation_id("o/r") == 8
    assert refreshing.repository_installation_id("o/r") == 8

    assert fresh.calls == ["repository_installation_id", "repository_installation_id"]
    assert reading.repository_installation_id("o/r") == 8
    assert stale.calls == ["repository_installation_id"]
