"""Адаптер-заглушка `LlmGateway`.

Временный: занимает место реального транспорта к Ollama, спроектированного
в `docs/BACKEND_ARCHITECTURE.md`. Возвращает фиксированный результат без
похода к какой-либо модели — реальный адаптер меняет только это, контракт
порта не затрагивает.
"""

from app.application.ports.llm_gateway import LlmReviewResult
from app.domain.entities import ContextPayload


class StubLlmGateway:
    def review(self, context: ContextPayload) -> LlmReviewResult:
        return LlmReviewResult(model="stub-llm", tokens_used=0, findings=())
