"""Какие доставки вебхука GitHub App переотправить (#149).

GitHub неудачные доставки сам не повторяет; переотправить можно доставку не
старше трёх дней, и переотправка сохраняет `guid` (`X-GitHub-Delivery`).
Решение — чистая функция над списком доставок: состояния у повтора нет, число
попыток и время последней выводятся из самого списка, поэтому рестарт воркера
ничего не сбрасывает и таблица не нужна.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import groupby

from app.domain.webhook import THREAD_ACTIONS, TRIGGERING_ACTIONS

# GitHub переотправляет доставки за последние трое суток; час запаса — проход
# идёт не мгновенно, а доставку на границе GitHub отклонит.
REDELIVERY_WINDOW = timedelta(days=3) - timedelta(hours=1)

# События, которые обрабатывает приём (`app/api/webhooks.py`), и их действия.
# Остальные переотправлять незачем: приём ответил бы `ignored`.
REDELIVERED_EVENTS: dict[str, frozenset[str]] = {
    "pull_request": TRIGGERING_ACTIONS,
    "pull_request_review_thread": THREAD_ACTIONS,
}

# `status` успешной доставки в ответе GitHub (пример переотправки в документации
# GitHub: «status value that is not OK» — неудача).
_SUCCESS_STATUS = "OK"


@dataclass(frozen=True, slots=True)
class WebhookDelivery:
    """Одна попытка доставки из `GET /app/hook/deliveries`."""

    id: int
    guid: str
    delivered_at: datetime
    redelivery: bool
    status: str
    # Без ответа получателя GitHub пишет 502 и `status` «failed to connect to host»
    # (проверено на репозиторном API доставок 2026-10-24).
    status_code: int
    event: str
    action: str | None

    @property
    def succeeded(self) -> bool:
        return self.status == _SUCCESS_STATUS or 200 <= self.status_code < 300


@dataclass(frozen=True, slots=True)
class RedeliveryPolicy:
    """Пределы повтора: попытки на `guid`, базовая пауза, переотправки за проход.

    `limit` считает настоящие переотправки и применяется сценарием прохода, а не
    `select_redeliveries`: кандидат может отсеяться уже после выбора (устарел),
    и лимит по кандидатам дал бы устаревшим вытеснить нужную доставку.
    """

    max_attempts: int
    backoff: timedelta
    limit: int
    window: timedelta = REDELIVERY_WINDOW


def select_redeliveries(
    deliveries: Iterable[WebhookDelivery], now: datetime, policy: RedeliveryPolicy
) -> list[WebhookDelivery]:
    """Все попытки, которые пора переотправить: по одной на `guid`, старые первыми.

    Группа `guid` переотправляется, если в ней нет успешной попытки, событие
    обрабатывает приём, первая попытка моложе окна, переотправок (`redelivery`)
    меньше `max_attempts`, и с последней попытки прошло `backoff × 2^переотправок`.
    Переотправляется последняя попытка группы. Порядок — по первой попытке:
    после простоя доставки одного PR приходят в порядке push'ей. `policy.limit`
    здесь не применяется — его считает сценарий по сделанным переотправкам.
    """
    by_guid = sorted(deliveries, key=lambda d: (d.guid, d.delivered_at, d.id))
    due: list[tuple[datetime, WebhookDelivery]] = []
    for _, group in groupby(by_guid, key=lambda d: d.guid):
        attempts = list(group)
        first, last = attempts[0], attempts[-1]
        if any(a.succeeded for a in attempts):
            continue
        if first.action not in REDELIVERED_EVENTS.get(first.event, frozenset()):
            continue
        if now - first.delivered_at > policy.window:
            continue
        redelivered = sum(1 for a in attempts if a.redelivery)
        if redelivered >= policy.max_attempts:
            continue
        if now - last.delivered_at < policy.backoff * 2**redelivered:
            continue
        due.append((first.delivered_at, last))
    due.sort(key=lambda item: (item[0], item[1].id))
    return [last for _, last in due]
