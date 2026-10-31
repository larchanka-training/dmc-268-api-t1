"""Адаптер `CacheStore`: словарь в памяти процесса с TTL.

Кэш одного процесса (§4.3): умирает с рестартом, и это штатно — промах не
ошибка; общий для реплик — адаптер Redis. В API к нему обращаются потоки
threadpool разом, поэтому чтение и запись — под замком. Порт не
импортируется — совпадение структурное.
Часы приходят инъекцией (`monotonic`), чтобы тест проверял истечение TTL
без ожидания. Объём ограничен числом записей и, если заданы, байтами: при
переполнении сначала выметаются истёкшие записи, затем самые старые — память
процесса не растёт с каждым новым sha. Размер записи — длина значения в UTF-8;
запись больше `max_entry_bytes` (без него — больше `max_bytes`) не сохраняется
вовсе.
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
        max_bytes: int | None = None,
        max_entry_bytes: int | None = None,
    ) -> None:
        self._monotonic = monotonic
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_entry_bytes = max_entry_bytes
        # Ключ → (истекает, значение, размер в байтах).
        self._entries: dict[str, tuple[float, str, int]] = {}
        self._total_bytes = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value, _ = entry
            if self._monotonic() >= expires_at:
                self._remove(key)
                return None
            return value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        size = len(value.encode())
        with self._lock:
            # Перезапись переносит ключ в конец порядка вставки — он «свежий»;
            # прежняя запись уходит и тогда, когда новая не помещается.
            self._remove(key)
            # Не помещающаяся запись не вставляется: иначе выметание сначала
            # вычистило бы все остальные, а потом и её саму.
            if size > self._entry_limit():
                return
            self._entries[key] = (self._monotonic() + ttl_seconds, value, size)
            self._total_bytes += size
            if self._overflowing():
                self._evict()

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._total_bytes -= entry[2]

    def _entry_limit(self) -> float:
        """Наибольший размер одной записи: свой лимит, иначе общий, иначе без предела."""
        if self._max_entry_bytes is not None:
            return self._max_entry_bytes
        if self._max_bytes is not None:
            return self._max_bytes
        return float("inf")

    def _overflowing(self) -> bool:
        if len(self._entries) > self._max_entries:
            return True
        return self._max_bytes is not None and self._total_bytes > self._max_bytes

    def _evict(self) -> None:
        now = self._monotonic()
        for key in [k for k, (expires_at, _, _) in self._entries.items() if now >= expires_at]:
            self._remove(key)
        while self._overflowing():
            self._remove(next(iter(self._entries)))
