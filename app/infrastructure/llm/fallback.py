"""Составной `LlmGateway`: провайдеры по порядку, первый ответ побеждает.

Порядок собирает composition root, и выбора там нет: локальная модель
первая, hosted-провайдер - только вторым и только при явном включении
(change `add-llm-gateway`, design D3), или он один при `LLM_BACKEND=eurorouter`
(change `eurorouter-primary-backend`). Здесь лишь применяется порядок.

Сбой модели любого класса (`LlmError`, включая `LlmOutputError`) передаёт тот
же запрос следующему провайдеру; после последнего пробрасывается его сбой.
Прочие исключения - ошибки программы - не гасятся и дальше не передаются.
Расход сбойных провайдеров уже в счётчике запроса: каждый провайдер пишет
туда свои ответы сам, и здесь его не суммируют. Переход к следующему
провайдеру - повтор вызова: о нём узнаёт `request.on_retry`.

Каждый фактический вызов провайдера пишется в гистограмму
`review_llm_call_duration` с исходом (`ok`/`error`) и моделью вызова:
у ответа - модель ответа, у сбоя - имя провайдера. Метрика времени каждого
вызова модели (аудит #89) видит и таймаут Ollama, и ответ Eurorouter двумя
записями. Токены метрикой не собираются: метрики по каждому вызову
провайдера - #119.
"""

import logging
import time
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from app.application.ports.llm_gateway import LlmGateway, LlmRequest, LlmResponse
from app.domain.llm_errors import LlmError
from app.infrastructure.telemetry import record_llm_call_duration

logger = logging.getLogger(__name__)


class FallbackLlmGateway:
    def __init__(
        self,
        providers: Sequence[tuple[str, LlmGateway]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not providers:
            raise ValueError("нужен хотя бы один провайдер модели")
        self._providers = tuple(providers)
        self._clock = clock

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        *heads, (_, last) = self._providers
        for (name, provider), (next_name, _) in zip(heads, self._providers[1:], strict=True):
            started = self._clock()
            try:
                response = provider.complete(request)
            except LlmError as error:
                record_llm_call_duration(
                    request.task.value, name, self._clock() - started, outcome="error"
                )
                if request.on_retry is not None:
                    request.on_retry(error)
                logger.warning(
                    "модель %s не ответила на %s (%s, код %s), запрос уходит %s",
                    name,
                    request.task,
                    type(error).__name__,
                    error.code,
                    next_name,
                )
            else:
                record_llm_call_duration(
                    request.task.value,
                    name,
                    self._clock() - started,
                    outcome="ok",
                    model=response.model,
                )
                return response
        (last_name, _) = self._providers[-1]
        started = self._clock()
        try:
            response = last.complete(request)
        except LlmError:
            record_llm_call_duration(
                request.task.value, last_name, self._clock() - started, outcome="error"
            )
            raise
        record_llm_call_duration(
            request.task.value,
            last_name,
            self._clock() - started,
            outcome="ok",
            model=response.model,
        )
        return response
