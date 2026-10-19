"""Порт извлечения контура файла: определения с диапазонами строк и импорты.

Парсер (tree-sitter) — деталь адаптера инфраструктуры; домен и приложение
говорят только доменными типами. Порт — шов Strangler Fig: если биндинги
разочаруют, парсинг уедет в отдельный сервис сменой адаптера
(`docs/BACKEND_ARCHITECTURE.md`, «Когда действительно стоит разделять»).
Какие определения показать модели, решает не адаптер, а
`app.domain.code_context.select_scopes`.
"""

from typing import Protocol

from app.domain.entities import FileOutline
from app.domain.enums import Language


class DefinitionExtractor(Protocol):
    def extract(self, source: str, language: Language) -> FileOutline:
        """Контур файла. Синтаксическая ошибка не исключение: парсер отдаёт то,
        что смог разобрать."""
        ...
