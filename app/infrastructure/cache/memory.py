"""Адаптер `CacheStore` в памяти процесса.

Порт не импортируется — совпадение структурное (`.agents/rules/backend.md`,
«Швы и порты»). Заменяет Redis ровно до тех пор, пока сервис — одна реплика:
в памяти одной реплики кэш и так общий для всех запросов, а отдельный
вендорский клиент не нужен (design `add-webhook-intake-and-vcs-gateway`, D7).
Вытеснение — по `monotonic`: TTL отсчитывается от времени процесса, скачки
настенных часов его не сдвигают.
"""

import time
from collections.abc import Callable


class InMemoryCacheStore:
    """dict с expiry; просроченные записи читаются как отсутствующие."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._entries: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= self._monotonic():
            del self._entries[key]
            return None
        return value

    def put(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self._entries[key] = (value, self._monotonic() + ttl_seconds)
