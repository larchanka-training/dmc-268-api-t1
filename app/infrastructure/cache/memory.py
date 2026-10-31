"""Адаптер `CacheStore`: словарь в памяти процесса с TTL.

Кэш одного процесса (§4.3): умирает с рестартом, и это штатно — промах не
ошибка; общий для реплик — адаптер Redis. В API к нему обращаются потоки
threadpool разом, поэтому чтение и запись — под замком. Порт не
импортируется — совпадение структурное.
Часы приходят инъекцией (`monotonic`), чтобы тест проверял истечение TTL
без ожидания. Объём ограничен: при переполнении сначала выметаются истёкшие
записи, затем самые старые — память воркера не растёт с каждым новым sha.
"""

import threading
import time
from collections.abc import Callable


class InMemoryCacheStore:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        max_entries: int = 1024,
    ) -> None:
        self._monotonic = monotonic
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._monotonic() >= expires_at:
                del self._entries[key]
                return None
            return value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            # Перезапись переносит ключ в конец порядка вставки — он «свежий».
            self._entries.pop(key, None)
            self._entries[key] = (self._monotonic() + ttl_seconds, value)
            if len(self._entries) > self._max_entries:
                self._evict()

    def _evict(self) -> None:
        now = self._monotonic()
        for key in [k for k, (expires_at, _) in self._entries.items() if now >= expires_at]:
            del self._entries[key]
        while len(self._entries) > self._max_entries:
            del self._entries[next(iter(self._entries))]
