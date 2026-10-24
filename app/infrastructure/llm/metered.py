"""Обёртка `LlmGateway`, которая пишет расход токенов и latency модели в бизнес-метрики.

Метрики — ручной телеметрией из инфраструктуры (спека `observability`): слой
приложения про OpenTelemetry не знает, а адаптеры провайдеров знают только
свой HTTP-вызов. Обёртка стоит над готовым шлюзом в composition root и видит
итог логического вызова один раз: приложение зовёт `complete` по одному разу
на логический вызов, схемные повторы и переключение провайдеров происходят
внутри шлюза и свёрнуты в длительность этой точки — отдельно попытки видны
слушателю `on_retry` и в токенах ответа. Сбойный вызов метрик не получает: его
токены доходят до прогонной записи через исключение, а не через ответ
(issue #119 — разбивка по типам токенов и стоимость).
"""

import time

from pydantic import BaseModel

from app.application.ports.llm_gateway import LlmGateway, LlmRequest, LlmResponse
from app.infrastructure.telemetry import record_llm_call_duration, record_llm_tokens


class MeteredLlmGateway:
    """Декорирует шлюз и учитывает токены и длительность успешных вызовов."""

    def __init__(self, gateway: LlmGateway) -> None:
        self._gateway = gateway

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        started = time.monotonic()
        response = self._gateway.complete(request)
        record_llm_call_duration(
            task=request.task.value,
            model=response.model,
            duration_seconds=time.monotonic() - started,
        )
        record_llm_tokens(
            task=request.task.value, model=response.model, tokens=response.tokens_used
        )
        return response
