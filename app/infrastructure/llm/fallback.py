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
записями. Токены метрикой не собираются: токены и стоимость - в базе на прогоне (#119).

У провайдера может быть свой breaker (#294): провайдер на паузе пропускается
без вызова и без записи в гистограмму, а если на паузе все - сбой
`LlmUnavailableError` сразу, без таймаутов. Исход вызова идёт в breaker:
`LlmUnavailableError` (сеть, таймаут, 5xx) - отказ, любой другой исход -
провайдер ответил.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence

from pydantic import BaseModel

from app.application.circuit_breaker import CircuitBreaker
from app.application.ports.llm_gateway import LlmGateway, LlmRequest, LlmResponse
from app.domain.llm_errors import LlmError, LlmUnavailableError
from app.infrastructure.telemetry import record_llm_call_duration

logger = logging.getLogger(__name__)


class FallbackLlmGateway:
    def __init__(
        self,
        providers: Sequence[tuple[str, LlmGateway]],
        *,
        clock: Callable[[], float] = time.monotonic,
        breakers: Mapping[str, CircuitBreaker] | None = None,
    ) -> None:
        """`breakers` - breaker провайдера по его имени; у провайдера без записи его нет."""
        if not providers:
            raise ValueError("нужен хотя бы один провайдер модели")
        self._providers = tuple(providers)
        self._clock = clock
        self._breakers = dict(breakers or {})

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        providers = [(name, provider) for name, provider in self._providers if not self._paused(name)]
        if not providers:
            raise LlmUnavailableError("все провайдеры модели на паузе breaker'а")
        *heads, (_, last) = providers
        for (name, provider), (next_name, _) in zip(heads, providers[1:], strict=True):
            started = self._clock()
            try:
                response = self._call(name, provider, request)
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
        (last_name, _) = providers[-1]
        started = self._clock()
        try:
            response = self._call(last_name, last, request)
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

    def _paused(self, name: str) -> bool:
        breaker = self._breakers.get(name)
        if breaker is None or (wait := breaker.retry_after()) is None:
            return False
        # Пауза может длиться сутками при живом фоллбэке: строка на каждый вызов — шум,
        # переходы breaker'а пишет `CircuitBreaker`.
        logger.debug("модель %s на паузе breaker'а ещё %.0f с, вызов пропущен", name, wait, extra={"provider": name})
        return True

    def _call[T: BaseModel](self, name: str, provider: LlmGateway, request: LlmRequest[T]) -> LlmResponse[T]:
        """Вызов провайдера с записью исхода в его breaker."""
        breaker = self._breakers.get(name)
        if breaker is None:
            return provider.complete(request)
        try:
            response = provider.complete(request)
        except LlmUnavailableError:
            breaker.record_failure()
            raise
        except LlmError:
            breaker.record_answer()
            raise
        breaker.record_answer()
        return response
