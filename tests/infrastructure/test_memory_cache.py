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


def test_entry_larger_than_entry_limit_is_not_stored_and_drops_previous() -> None:
    cache = InMemoryCacheStore(max_entry_bytes=4)
    cache.set("k", "old", ttl_seconds=100)
    cache.set("k", "12345", ttl_seconds=100)
    assert cache.get("k") is None


def test_entry_size_is_utf8_length_not_character_count() -> None:
    # «ё» — два байта в UTF-8: три символа — шесть байт.
    cache = InMemoryCacheStore(max_entry_bytes=5)
    cache.set("k", "ёёё", ttl_seconds=100)
    assert cache.get("k") is None
    cache.set("k", "ёё", ttl_seconds=100)
    assert cache.get("k") == "ёё"


def test_byte_overflow_evicts_expired_entries_first() -> None:
    clock = _Clock()
    cache = InMemoryCacheStore(monotonic=clock, max_bytes=6)
    cache.set("short", "aaa", ttl_seconds=1)
    cache.set("long", "bbb", ttl_seconds=100)
    clock.now = 5
    cache.set("fresh", "ccc", ttl_seconds=100)
    assert cache.get("long") == "bbb"
    assert cache.get("fresh") == "ccc"


def test_byte_overflow_evicts_oldest_until_limit_holds() -> None:
    cache = InMemoryCacheStore(max_bytes=6)
    cache.set("a", "11", ttl_seconds=100)
    cache.set("b", "22", ttl_seconds=100)
    cache.set("c", "33", ttl_seconds=100)
    cache.set("d", "4444", ttl_seconds=100)
    assert (cache.get("a"), cache.get("b")) == (None, None)
    assert (cache.get("c"), cache.get("d")) == ("33", "4444")


def test_overwrite_recounts_entry_size() -> None:
    cache = InMemoryCacheStore(max_bytes=6)
    cache.set("a", "11111", ttl_seconds=100)
    cache.set("a", "1", ttl_seconds=100)
    cache.set("b", "22222", ttl_seconds=100)
    assert (cache.get("a"), cache.get("b")) == ("1", "22222")


def test_entry_count_limit_still_applies_with_byte_limit() -> None:
    cache = InMemoryCacheStore(max_entries=2, max_bytes=1000)
    cache.set("a", "1", ttl_seconds=100)
    cache.set("b", "2", ttl_seconds=100)
    cache.set("c", "3", ttl_seconds=100)
    assert cache.get("a") is None
    assert (cache.get("b"), cache.get("c")) == ("2", "3")


def test_value_larger_than_whole_cache_is_not_stored_and_keeps_others() -> None:
    cache = InMemoryCacheStore(max_bytes=4)
    cache.set("a", "11", ttl_seconds=100)
    cache.set("b", "12345", ttl_seconds=100)
    assert cache.get("a") == "11"
    assert cache.get("b") is None
