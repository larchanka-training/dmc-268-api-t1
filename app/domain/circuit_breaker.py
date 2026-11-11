"""Circuit breaker провайдера: решение по данным, без часов и ввода-вывода (#294).

Состояние — неизменяемая запись, переходы — чистые функции: время приходит
аргументом (монотонные секунды), поэтому тест сверяет точный момент пробы.
Что считать отказом, решает адаптер — домен знает только «отказ» и «ответ».

- **closed** — вызовы идут; `failure_threshold` отказов подряд размыкают.
- **open** — до `open_until` вызовы не идут: прогон откладывается, а не ждёт таймаутов.
- **half_open** — пауза вышла, исход неизвестен: вызов идёт пробным. Ответ
  замыкает, отказ снова размыкает — с паузой вдвое длиннее, до `max_pause_seconds`.
"""

from dataclasses import dataclass, replace
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


@dataclass(frozen=True, slots=True)
class BreakerPolicy:
    failure_threshold: int
    pause_seconds: float
    max_pause_seconds: float

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("порог отказов breaker'а — от 1")
        if not 0 < self.pause_seconds <= self.max_pause_seconds:
            raise ValueError("пауза breaker'а — больше 0 и не больше потолка")


@dataclass(frozen=True, slots=True)
class Breaker:
    # Отказов подряд с последнего ответа.
    failures: int = 0
    # Размыканий подряд без ответа между ними: растит паузу.
    trips: int = 0
    # Конец паузы; `None` — замкнут.
    open_until: float | None = None


# Провайдер ответил — каким угодно ответом, хоть 4xx: он жив, breaker замкнут и счёт обнулён.
CLOSED = Breaker()


def state(breaker: Breaker, now: float) -> BreakerState:
    if breaker.open_until is None:
        return BreakerState.CLOSED
    return BreakerState.OPEN if now < breaker.open_until else BreakerState.HALF_OPEN


def retry_after(breaker: Breaker, now: float) -> float | None:
    """Секунды до пробного вызова; `None` — вызов можно делать сейчас."""
    if breaker.open_until is None or now >= breaker.open_until:
        return None
    return breaker.open_until - now


def failed(breaker: Breaker, policy: BreakerPolicy, now: float) -> Breaker:
    """Отказ провайдера: счёт подряд растёт; на пороге или после паузы — размыкание."""
    failures = breaker.failures + 1
    if breaker.open_until is None and failures < policy.failure_threshold:
        return replace(breaker, failures=failures)
    if breaker.open_until is not None and now < breaker.open_until:
        # Вызов, начатый до размыкания, отказал уже на паузе: пауза не удлиняется.
        return replace(breaker, failures=failures)
    trips = breaker.trips + 1
    pause = min(policy.pause_seconds * 2 ** (trips - 1), policy.max_pause_seconds)
    return Breaker(failures=failures, trips=trips, open_until=now + pause)
