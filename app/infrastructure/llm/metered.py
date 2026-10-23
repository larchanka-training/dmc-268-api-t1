"""Обёртка `LlmGateway`, которая пишет расход токенов модели в бизнес-метрики.

Метрики — ручной телеметрией из инфраструктуры (спека `observability`): слой
приложения про OpenTelemetry не знает, а адаптеры провайдеров знают только
свой HTTP-вызов. Обёртка стоит над готовым шлюзом в composition root и видит
итог вызова `complete` один раз: токены повторов и фоллбэка уже просуммированы
в ответе. Сбойный вызов метрики не получает: токены сбоя доходят до прогонной
записи через исключение, а не через ответ (issue #119 — разбивка по типам
токенов и стоимость).
"""

from pydantic import BaseModel

from app.application.ports.llm_gateway import LlmGateway, LlmRequest, LlmResponse
from app.infrastructure.telemetry import record_llm_tokens


class MeteredLlmGateway:
    """Декорирует шлюз и учитывает токены успешных вызовов по модели и задаче."""

    def __init__(self, gateway: LlmGateway) -> None:
        self._gateway = gateway

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        response = self._gateway.complete(request)
        record_llm_tokens(
            task=request.task.value, model=response.model, tokens=response.tokens_used
        )
        return response
