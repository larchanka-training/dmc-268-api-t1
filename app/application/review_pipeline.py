"""Оркестрация одного прогона ревью: queued → ... → completed | failed.

Use case уровня application: знает порты (`UnitOfWork`, `LlmGateway`) и
чистые функции домена (`advance`), не знает ни одного адаптера. Каждый
переход — свой вход в `with uow:` и свой `commit`, поэтому прогресс,
записанный до сбоя, не откатывается вместе с сбойным шагом: если воркер
упадёт после `analysing`, база уже знает об этом, и `find_stale` сможет
когда-нибудь освободить коммит.
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.application.context_assembly import assemble_context_stub, stub_hunks
from app.application.ports import UnitOfWork
from app.application.ports.llm_gateway import LlmGateway
from app.domain.entities import Finding, ReviewJob, ReviewRun
from app.domain.enums import TERMINAL_STATUSES, ReviewRunStatus
from app.domain.lifecycle import advance

logger = logging.getLogger(__name__)


def _transition(
    uow: UnitOfWork, run: ReviewRun, status: ReviewRunStatus, now: datetime
) -> ReviewRun:
    new_run = advance(run, status, now).unwrap()
    with uow:
        uow.review_runs.update(new_run)
        uow.commit()
    return new_run


def _mark_failed(uow: UnitOfWork, run_id: UUID, exc: Exception, now: datetime) -> None:
    """Перевести прогон в failed по свежей строке из базы.

    Копия прогона в памяти могла разойтись с базой (строка ушла вперёд), и
    переход от неё бросил бы сам. Терминальный прогон не трогаем. Сбой этой
    записи (например, база недоступна) логируется и не подменяет исходное
    исключение — зависший прогон выметет `sweep_stale_runs`.
    """
    try:
        with uow:
            run = uow.review_runs.get(run_id)
            if run is None or run.status in TERMINAL_STATUSES:
                return
            failed = replace(
                advance(run, ReviewRunStatus.FAILED, now).unwrap(),
                failure_reason=str(exc) or type(exc).__name__,
            )
            uow.review_runs.update(failed)
            uow.commit()
    except Exception:
        logger.exception("не удалось перевести прогон %s в failed", run_id)


def run_review(
    job: ReviewJob,
    *,
    uow: UnitOfWork,
    llm_gateway: LlmGateway,
    now: Callable[[], datetime],
    monotonic: Callable[[], float],
    new_id: Callable[[], UUID],
) -> None:
    """Довести прогон до терминального состояния.

    Часы приходят функциями, как `new_id`: каждый переход берёт свой `now()`,
    поэтому `last_progress_at` отражает реальный прогресс (по нему
    `find_stale` отличает зависший прогон от идущего), а `duration_seconds` —
    разность монотонных часов, которую тест проверяет точно.

    Сбой любого шага переводит прогон в `failed` и **пробрасывается**: воркер
    отклоняет сообщение, и оно попадает в DLQ для разбора (`job-queue`).
    """
    with uow:
        run = uow.review_runs.get(job.review_run_id)

    if run is None:
        # Прогон коммитится до постановки задачи, поэтому сообщение без
        # строки — аномалия, а не гонка. Подтвердить его значило бы
        # уничтожить единственный след; исключение отправит его в DLQ.
        raise LookupError(f"review run {job.review_run_id} not found")
    if run.status in TERMINAL_STATUSES:
        # Прогон уже завершён — повторная доставка после завершения
        # игнорируется (review-data-model).
        return

    started_at = monotonic()

    try:
        run = _transition(uow, run, ReviewRunStatus.BUILDING_CONTEXT, now())

        context = assemble_context_stub(job, review_run_id=run.id, now=now(), new_id=new_id)
        with uow:
            uow.context_payloads.add(context)
            uow.commit()

        run = _transition(uow, run, ReviewRunStatus.ANALYSING, now())

        result = llm_gateway.review(context)
        hunks = stub_hunks()
        with uow:
            for item in result.findings:
                moment = now()
                finding = Finding(
                    id=new_id(),
                    review_run_id=run.id,
                    anchor=item.anchor,
                    category=item.category,
                    severity=item.severity,
                    message=item.message,
                    suggestion=item.suggestion,
                    confidence=item.confidence,
                    created_at=moment,
                    updated_at=moment,
                )
                # Отклонённая привязка учтена в прогоне и не прерывает
                # остальные находки.
                uow.findings.add_validated(finding, hunks, moment)
            uow.commit()

        run = _transition(uow, run, ReviewRunStatus.PUBLISHING, now())

        run = replace(
            run,
            model=result.model,
            tokens_used=result.tokens_used,
            duration_seconds=monotonic() - started_at,
        )
        _transition(uow, run, ReviewRunStatus.COMPLETED, now())
    except Exception as exc:
        _mark_failed(uow, job.review_run_id, exc, now())
        raise
