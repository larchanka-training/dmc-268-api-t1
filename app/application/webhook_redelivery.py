"""Повтор упавших доставок вебхука (#149).

GitHub неудачные доставки сам не повторяет. Проход воркера читает попытки
доставки за окно, чистая `select_redeliveries` решает, какие переотправить, а
здесь — только запросы, по одному: вторичные лимиты GitHub считаются на токен.

Устаревшая доставка не переотправляется: push A упал, push B прошёл — повтор A
не нашёл бы прогона на A, приём сдвинул бы head merge_request назад и
отревьюил старый коммит. Поэтому у кандидата `pull_request` проход читает
payload доставки и сверяет `pull_request.head.sha` со свежей головой PR
(`VcsGateway.fetch_pr_metadata`). Проверка — здесь, а не в приёме: свежая
доставка сразу после push'а могла бы увидеть у GitHub ещё прежнюю голову и
потеряться молча, а повтор идёт через минуты после сбоя. У
`pull_request_review_thread` головы нет — он переотправляется без сверки.

Временный сбой (`VcsTransientError`: лимит, сеть, 5xx) любого из запросов
завершает проход; прочий отказ — пропуск кандидата, проход идёт дальше.
Проход ограничен и бюджетом времени по монотонным часам: истёк — оставшиеся
кандидаты ждут следующего прохода, consume-цикл воркера не стоит.

Несколько воркеров могут переотправить одну доставку в одном окне: приём
ответит существующим прогоном (дубль, #75), лишняя попытка лишь быстрее
исчерпает `max_attempts`.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.webhook_deliveries import WebhookDeliveries
from app.domain.vcs_errors import VcsError, VcsTransientError
from app.domain.webhook import extract_github_event, is_trusted_author
from app.domain.webhook_redelivery import (
    RedeliveryPolicy,
    WebhookDelivery,
    select_redeliveries,
)

logger = logging.getLogger(__name__)

# Событие, у которого есть голова PR и которое может устареть.
_PULL_REQUEST_EVENT = "pull_request"


def redeliver_failed_webhooks(
    hooks: WebhookDeliveries,
    vcs: VcsGateway,
    *,
    now: datetime,
    policy: RedeliveryPolicy,
    monotonic: Callable[[], float],
    budget_seconds: float,
    known_stale: set[str],
) -> int:
    """Переотправить упавшие доставки по `policy`; вернуть число принятых переотправок.

    `policy.limit` считает настоящие переотправки, а не кандидатов: проход идёт
    по всем выбранным, пока не сделает `limit` переотправок или не истечёт
    бюджет. `monotonic` и `budget_seconds` ограничивают проход по времени:
    бюджет проверяется перед каждым кандидатом, отсчёт — с начала прохода,
    вместе со списком доставок.

    `known_stale` — `guid`, признанные устаревшими, без PR в payload или с
    постоянным (не временным) отказом, в памяти процесса (живёт у вызывающего
    между проходами, урезается до текущих кандидатов): такие кандидаты
    пропускаются без запросов, сюда же сценарий дописывает новые. Доставка не
    перестаёт быть устаревшей, а без памяти она каждые пять минут трое суток
    стоила бы два запроса.
    """
    started = monotonic()
    candidates = select_redeliveries(hooks.list_recent(now - policy.window), now, policy)
    # На месте: множество — вызывающего. `guid`, вышедший из окна, забывается.
    known_stale &= {delivery.guid for delivery in candidates}
    due = [delivery for delivery in candidates if delivery.guid not in known_stale]
    accepted = 0
    for position, delivery in enumerate(due):
        if accepted >= policy.limit:
            break
        if monotonic() - started >= budget_seconds:
            logger.info(
                "бюджет прохода повтора доставок исчерпан (%g с), отложено доставок: %d",
                budget_seconds,
                len(due) - position,
            )
            break
        try:
            if delivery.event == _PULL_REQUEST_EVENT and not _still_current(hooks, vcs, delivery):
                known_stale.add(delivery.guid)
                continue
            hooks.redeliver(delivery.id)
        except VcsTransientError:
            raise
        except VcsError as error:
            # Доставка могла выйти из окна GitHub (422), PR — исчезнуть (404):
            # отказ постоянный, кандидат запоминается и больше не стоит запросов.
            logger.warning("переотправка доставки %s (%s) пропущена: %s", delivery.id, delivery.guid, error)
            known_stale.add(delivery.guid)
            continue
        accepted += 1
    return accepted


def _still_current(hooks: WebhookDeliveries, vcs: VcsGateway, delivery: WebhookDelivery) -> bool:
    """Коммит доставки `pull_request` — всё ещё голова PR."""
    payload = hooks.delivery_payload(delivery.id)
    event = extract_github_event(payload) if payload is not None else None
    if event is None or not is_trusted_author(event):
        # Действие не запускает ревью, автор PR не свой или payload не разобран —
        # приём ответил бы `ignored`, переотправлять нечего.
        logger.warning(
            "доставка %s (%s) не запускает ревью (действие или автор), не переотправляется", delivery.id, delivery.guid
        )
        return False
    head = vcs.fetch_pr_metadata(event.repo_full_name, event.pr_number, event.installation_id).head_sha
    if head != event.head_sha:
        logger.info(
            "доставка устарела: голова PR уже другая (доставка %s, %s#%d: %s вместо %s)",
            delivery.id,
            event.repo_full_name,
            event.pr_number,
            head,
            event.head_sha,
        )
        return False
    return True
