"""In-process `CacheStore`: запись, чтение, TTL и ограничение объёма — без ожиданий."""

from app.infrastructure.cache.memory import InMemoryCacheStore


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_value_is_readable_until_ttl_expires() -> None:
    clock = _Clock()
    cache = InMemoryCacheStore(monotonic=clock)
    cache.set("k", "v", ttl_seconds=10)
    clock.now = 9.9
    assert cache.get("k") == "v"
    clock.now = 10.0
    assert cache.get("k") is None


def test_missing_key_is_a_miss_not_an_error() -> None:
    assert InMemoryCacheStore().get("nope") is None


def test_overwrite_replaces_value_and_ttl() -> None:
    clock = _Clock()
    cache = InMemoryCacheStore(monotonic=clock)
    cache.set("k", "old", ttl_seconds=1)
    cache.set("k", "new", ttl_seconds=100)
    clock.now = 50
    assert cache.get("k") == "new"


def test_overflow_evicts_expired_entries_first() -> None:
    clock = _Clock()
    cache = InMemoryCacheStore(monotonic=clock, max_entries=2)
    cache.set("short", "1", ttl_seconds=1)
    cache.set("long", "2", ttl_seconds=100)
    clock.now = 5
    cache.set("fresh", "3", ttl_seconds=100)
    assert cache.get("long") == "2"
    assert cache.get("fresh") == "3"


def test_overflow_evicts_the_oldest_live_entry() -> None:
    cache = InMemoryCacheStore(max_entries=2)
    cache.set("a", "1", ttl_seconds=100)
    cache.set("b", "2", ttl_seconds=100)
    cache.set("c", "3", ttl_seconds=100)
    assert cache.get("a") is None
    assert (cache.get("b"), cache.get("c")) == ("2", "3")
