"""Двойники сборки контекста: кэш и парсер без tree-sitter."""

from dataclasses import dataclass, field

from app.domain.entities import FileOutline
from app.domain.enums import Language


@dataclass
class FakeCache:
    """Словарь без TTL, записывающий обращения; `broken` — хранилище недоступно."""

    data: dict[str, str] = field(default_factory=dict)
    broken: bool = False
    sets: list[tuple[str, str, int]] = field(default_factory=list)

    def get(self, key: str) -> str | None:
        if self.broken:
            raise ConnectionError("кэш недоступен")
        return self.data.get(key)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if self.broken:
            raise ConnectionError("кэш недоступен")
        self.sets.append((key, value, ttl_seconds))
        self.data[key] = value


@dataclass
class FakeExtractor:
    """Контур по исходнику; неизвестный исходник — пустой контур."""

    outlines: dict[str, FileOutline] = field(default_factory=dict)
    error: Exception | None = None
    calls: list[tuple[str, Language]] = field(default_factory=list)

    def extract(self, source: str, language: Language) -> FileOutline:
        self.calls.append((source, language))
        if self.error is not None:
            raise self.error
        return self.outlines.get(source, FileOutline())
