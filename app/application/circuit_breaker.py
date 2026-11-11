"""Breaker одного провайдера: состояние из домена, часы и блокировка — здесь (#294).

Адаптер спрашивает `retry_after()` перед вызовом и сообщает исход —
`record_answer()` или `record_failure()`; какие ошибки — отказ, решает он
(`design.md` change `circuit-breaker`). Состояние общее для потоков процесса:
обработчик сообщения и проход очистки воркера зовут один GitHub.

`deferral` — решение воркера до захвата прогона: ждать ли провайдеров.
"""

import logging
import threading
from collections.abc import Callable, Sequence

from app.domain import circuit_breaker as breaker
from app.domain.circuit_breaker import BreakerPolicy, BreakerState

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(self, provider: str, policy: BreakerPolicy, *, clock: Callable[[], float]) -> None:
        """`clock` — монотонные секунды; в тестах — подменные часы."""
        self.provider = provider
        self._policy = policy
        self._clock = clock
        self._state = breaker.CLOSED
        self._lock = threading.Lock()

    def state(self) -> BreakerState:
        with self._lock:
            return breaker.state(self._state, self._clock())

    def retry_after(self) -> float | None:
        """Секунды до пробного вызова; `None` — вызывать можно."""
        with self._lock:
            return breaker.retry_after(self._state, self._clock())

    def record_answer(self) -> None:
        with self._lock:
            was_open = self._state.open_until is not None
            self._state = breaker.answered(self._state)
        if was_open:
            logger.info("breaker %s замкнут: провайдер ответил", self.provider, extra={"provider": self.provider})

    def record_failure(self) -> None:
        with self._lock:
            before = self._state
            self._state = breaker.failed(before, self._policy, self._clock())
            after = self._state
        if after.open_until != before.open_until and after.open_until is not None:
            logger.warning(
                "breaker %s разомкнут после %d отказов подряд: вызовы на паузе %.0f с",
                self.provider,
                after.failures,
                after.open_until - self._clock(),
                extra={"provider": self.provider},
            )


def deferral(vcs: CircuitBreaker | None, llm: Sequence[CircuitBreaker]) -> float | None:
    """Через сколько секунд прогону есть смысл идти; `None` — сейчас.

    Прогону нужны и GitHub, и модель. Модель — любой провайдер фоллбэка: ждём,
    только если на паузе все, и до ближайшей пробы. Без breaker'ов — не ждём.
    """
    waits: list[float] = []
    if vcs is not None and (wait := vcs.retry_after()) is not None:
        waits.append(wait)
    llm_waits = [b.retry_after() for b in llm]
    if llm_waits and all(w is not None for w in llm_waits):
        waits.append(min(w for w in llm_waits if w is not None))
    return max(waits) if waits else None
