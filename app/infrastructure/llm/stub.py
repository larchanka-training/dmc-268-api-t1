"""Адаптер-заглушка `LlmGateway`.

Временный: занимает место адаптеров к провайдерам (задача «LLM Gateway»).
Без похода к какой-либо модели отвечает пустым, но корректным экземпляром
запрошенной модели ответа: ревью без замечаний, верификация без вердиктов.
Реальный адаптер меняет только это, контракт порта не затрагивает.
"""

from typing import Any

from pydantic import BaseModel

from app.application.ports.llm_gateway import LlmRequest, LlmResponse, LlmTask

_EMPTY: dict[LlmTask, dict[str, Any]] = {
    LlmTask.REVIEW: {
        "schemaVersion": "1",
        "findings": [],
        "summary": {
            "problem": "Критичных проблем в диффе не обнаружено.",
            "positives": [],
            "effort": "мерж без доработки по ревью бота",
        },
    },
    LlmTask.VERIFY: {"schemaVersion": "1", "verdicts": []},
}


class StubLlmGateway:
    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        return LlmResponse(
            model="stub-llm",
            tokens_used=0,
            content=request.response_model.model_validate(_EMPTY[request.task]),
        )
