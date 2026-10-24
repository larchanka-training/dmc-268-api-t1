"""Фейк порта `RunChanges`: подписка, ожидание которой задаёт тест."""

from collections.abc import Awaitable, Callable

from app.application.ports.run_changes import RunChangesLost


class FakeSubscription:
    def __init__(self, wait: Callable[[float], Awaitable[bool]]) -> None:
        self._wait = wait
        self.closed = False

    async def wait(self, timeout: float) -> bool:
        return await self._wait(timeout)

    def close(self) -> None:
        self.closed = True


class FakeRunChanges:
    """`wait` — ожидание сигнала; `available=False` — источник недоступен при подписке."""

    def __init__(self, wait: Callable[[float], Awaitable[bool]], *, available: bool = True) -> None:
        self._wait = wait
        self._available = available
        self.subscriptions: list[FakeSubscription] = []

    async def subscribe(self) -> FakeSubscription:
        if not self._available:
            raise RunChangesLost
        subscription = FakeSubscription(self._wait)
        self.subscriptions.append(subscription)
        return subscription


def every_step(sleep: Callable[[float], Awaitable[None]], step: float = 1.0) -> Callable[[float], Awaitable[bool]]:
    """Ожидание, которое шагает `step` секунд «сна» теста и каждый шаг сообщает об изменении."""

    async def wait(timeout: float) -> bool:
        await sleep(min(timeout, step))
        return True

    return wait
