"""Кэш списка видимых репозиториев поверх `InstallationCatalog` (#126). Без сети."""

import logging

import pytest

from app.domain.entities import InstallationRepository, RepositoryPermissions, Secret
from app.domain.vcs_errors import VcsError
from app.infrastructure.cache.memory import InMemoryCacheStore
from app.infrastructure.vcs.cached_installations import CachedInstallationCatalog

from ..fakes.context import FakeCache
from ..fakes.installation_catalog import FakeInstallationCatalog

TTL = 30
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
    return CachedInstallationCatalog(catalog, InMemoryCacheStore(monotonic=clock or Clock()), TTL)


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
    CachedInstallationCatalog(FakeInstallationCatalog([PRIVATE]), cache, TTL).list_repositories(TOKEN)

    [(key, value, ttl)] = cache.sets
    assert TOKEN.reveal() not in key
    assert TOKEN.reveal() not in value
    assert ttl == TTL


def test_a_github_failure_is_not_cached() -> None:
    failing = FakeInstallationCatalog(error=VcsError("HTTP 503"))
    cache = InMemoryCacheStore()

    with pytest.raises(VcsError):
        CachedInstallationCatalog(failing, cache, TTL).list_repositories(TOKEN)
    recovered = FakeInstallationCatalog([PRIVATE])

    assert CachedInstallationCatalog(recovered, cache, TTL).list_repositories(TOKEN) == [PRIVATE]
    assert recovered.calls == ["list_repositories"]


def test_a_broken_cache_is_a_miss_not_an_error(caplog: pytest.LogCaptureFixture) -> None:
    catalog = FakeInstallationCatalog([PRIVATE])
    cached = CachedInstallationCatalog(catalog, FakeCache(broken=True), TTL)

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
    reading = CachedInstallationCatalog(stale, cache, TTL)
    refreshing = CachedInstallationCatalog(fresh, cache, TTL, read=False)

    assert reading.list_repositories(TOKEN) == []
    assert refreshing.list_repositories(TOKEN) == [PRIVATE]
    assert refreshing.list_repositories(TOKEN) == [PRIVATE]

    assert fresh.calls == ["list_repositories", "list_repositories"]
    assert reading.list_repositories(TOKEN) == [PRIVATE]
    assert stale.calls == ["list_repositories"]


def test_the_installation_id_goes_past_the_cache() -> None:
    """Номер установки — всегда к GitHub: после переустановки приложения он другой."""
    catalog = FakeInstallationCatalog(installation_ids={"octo-org/api": 42})
    cache = FakeCache()
    cached = CachedInstallationCatalog(catalog, cache, TTL)

    assert cached.repository_installation_id("octo-org/api") == 42
    catalog.installation_ids["octo-org/api"] = 43
    assert cached.repository_installation_id("octo-org/api") == 43

    assert catalog.calls == ["repository_installation_id", "repository_installation_id"]
    assert cache.sets == []
