"""Порт транспорта к LLM для анализа диффа.

Отдельно от `app/application/ports/repositories.py` — не порт хранения, порт
внешней модели. Держится вне `ports.__all__` по той же причине, что и
`JobQueue`: гвардрейл в `tests/test_container.py` заточен под SQLAlchemy-
адаптеры портов хранения.

Первый (пока единственный) адаптер — `StubLlmGateway`: заглушка вместо
реального транспорта к Ollama, спроектированного в
`docs/BACKEND_ARCHITECTURE.md`.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain.entities import ContextPayload, DiffAnchor
from app.domain.enums import FindingCategory, FindingSeverity


@dataclass(frozen=True, slots=True)
class LlmFinding:
    """Находка, как её возвращает модель — ещё без id, прогона и меток времени."""

    anchor: DiffAnchor
    category: FindingCategory
    severity: FindingSeverity
    message: str
    suggestion: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class LlmReviewResult:
    model: str
    tokens_used: int
    findings: tuple[LlmFinding, ...]


class LlmGateway(Protocol):
    def review(self, context: ContextPayload) -> LlmReviewResult: ...
