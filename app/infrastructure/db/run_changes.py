"""Адаптер `RunChanges`: PostgreSQL `LISTEN` на канал записей прогонов.

Адаптер `review_runs` (`app/infrastructure/db/repositories.py`) вызывает
`pg_notify(REVIEW_RUNS_CHANNEL, id)` в транзакции единицы работы, поэтому
уведомление уходит только при коммите и после `rollback` его нет; повторы с тем
же id внутри транзакции PostgreSQL склеивает сам. Пишут прогоны и API, и воркер —
уведомление приходит от любого процесса.

Одно соединение `LISTEN` на процесс API будит всех подписчиков; что изменилось,
каждый поток дочитывает из базы своим курсором. Соединение открывается при
первой подписке: процесс, у которого нет открытых потоков, базу не слушает.
Обрыв — все текущие подписки получают `RunChangesLost` (уведомления за время
обрыва потеряны), соединение восстанавливается в фоне. Обрыв без RST ловят
пульс (`SELECT 1` раз в `HEARTBEAT_SECONDS` в тишине) и TCP keepalive. Соединение — psycopg
напрямую, мимо SQLAlchemy: ему нужен autocommit и асинхронный `notifies()`.
"""

import asyncio
import contextlib
import logging

import psycopg
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

from app.application.ports.run_changes import RunChangesLost

logger = logging.getLogger(__name__)

REVIEW_RUNS_CHANNEL = "review_runs"
# Имя соединения в `pg_stat_activity`: слушателя видно в диагностике.
LISTENER_APPLICATION_NAME = "dmc268-run-changes"
# Как часто проверять, что соединение живо: обрыв без RST (полуоткрытый TCP)
# иначе молча остановил бы события до закрытия сокета ОС.
HEARTBEAT_SECONDS = 30.0


def listen_conninfo(database_url: str) -> str:
    """Строка подключения psycopg из URL SQLAlchemy: `postgresql+psycopg://` → `postgresql://`."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


class _Subscription:
    def __init__(self, owner: PostgresRunChanges) -> None:
        self._owner = owner
        self._changed = asyncio.Event()
        self._lost = False

    def signal(self) -> None:
        self._changed.set()

    def lose(self) -> None:
        self._lost = True
        self._changed.set()

    async def wait(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
        except TimeoutError:
            return False
        if self._lost:
            raise RunChangesLost
        self._changed.clear()
        return True

    def close(self) -> None:
        self._owner.unsubscribe(self)


class PostgresRunChanges:
    def __init__(
        self,
        conninfo: str,
        *,
        connect_timeout: float = 5.0,
        reconnect_delay: float = 1.0,
        heartbeat: float = HEARTBEAT_SECONDS,
    ) -> None:
        # Тот же риск на уровне ОС: TCP keepalive закрывает мёртвое соединение за ~1 минуту.
        self._conninfo = make_conninfo(
            conninfo,
            application_name=LISTENER_APPLICATION_NAME,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
        )
        self._connect_timeout = connect_timeout
        self._reconnect_delay = reconnect_delay
        self._heartbeat = heartbeat
        self._subscribers: set[_Subscription] = set()
        self._listening = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def subscribe(self) -> _Subscription:
        if self._task is None:
            self._task = asyncio.create_task(self._listen_forever(), name="run-changes-listen")
        try:
            await asyncio.wait_for(self._listening.wait(), self._connect_timeout)
        except TimeoutError:
            raise RunChangesLost from None
        subscription = _Subscription(self)
        self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: _Subscription) -> None:
        self._subscribers.discard(subscription)

    async def close(self) -> None:
        """Остановить слушателя — из lifespan приложения."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _listen_forever(self) -> None:
        while True:
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Текст без строки подключения: в ней пароль.
                logger.warning("LISTEN %s оборвался, переподключение", REVIEW_RUNS_CHANNEL, exc_info=True)
            finally:
                self._drop_subscribers()
            await asyncio.sleep(self._reconnect_delay)

    async def _listen_once(self) -> None:
        async with await psycopg.AsyncConnection.connect(self._conninfo, autocommit=True) as conn:
            await conn.execute(f"LISTEN {REVIEW_RUNS_CHANNEL}")
            self._listening.set()
            while True:
                async for _ in conn.notifies(timeout=self._heartbeat):
                    for subscription in list(self._subscribers):
                        subscription.signal()
                # Пульс: мёртвое соединение здесь даёт ошибку, а не вечную тишину.
                await conn.execute("SELECT 1")

    def _drop_subscribers(self) -> None:
        self._listening.clear()
        for subscription in list(self._subscribers):
            subscription.lose()
        self._subscribers.clear()
