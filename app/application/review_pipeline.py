"""Оркестрация одного прогона ревью: queued → ... → completed | failed.

Use case уровня application: знает порты (`UnitOfWork`, `LlmGateway`) и
чистые функции домена (`advance`), не знает ни одного адаптера. Каждый
переход — свой вход в `with uow:` и свой `commit`, поэтому прогресс,
записанный до сбоя, не откатывается вместе с сбойным шагом: если воркер
упадёт после `analysing`, база уже знает об этом, и `find_stale` сможет
когда-нибудь освободить коммит.
"""

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


def _transition(
    uow: UnitOfWork, run: ReviewRun, status: ReviewRunStatus, now: datetime
) -> ReviewRun:
    new_run = advance(run, status, now).unwrap()
    with uow:
        uow.review_runs.update(new_run)
        uow.commit()
    return new_run


def run_review(
    job: ReviewJob,
    *,
    uow: UnitOfWork,
    llm_gateway: LlmGateway,
    now: datetime,
    new_id: Callable[[], UUID],
) -> None:
    with uow:
        run = uow.review_runs.get(job.id)

    if run is None or run.status in TERMINAL_STATUSES:
        # Строки нет (сообщение без прогона) или прогон уже завершён —
        # повторная доставка после завершения игнорируется (review-data-model).
        return

    try:
        run = _transition(uow, run, ReviewRunStatus.BUILDING_CONTEXT, now)

        context = assemble_context_stub(job, review_run_id=run.id, now=now, new_id=new_id)
        with uow:
            uow.context_payloads.add(context)
            uow.commit()

        run = _transition(uow, run, ReviewRunStatus.ANALYSING, now)

        result = llm_gateway.review(context)
        hunks = stub_hunks()
        with uow:
            for item in result.findings:
                finding = Finding(
                    id=new_id(),
                    review_run_id=run.id,
                    anchor=item.anchor,
                    category=item.category,
                    severity=item.severity,
                    message=item.message,
                    suggestion=item.suggestion,
                    confidence=item.confidence,
                    created_at=now,
                    updated_at=now,
                )
                uow.findings.add_validated(finding, hunks, now)
            uow.commit()

        run = _transition(uow, run, ReviewRunStatus.PUBLISHING, now)

        duration = (now - run.created_at).total_seconds()
        run = replace(run, model=result.model, tokens_used=result.tokens_used, duration_seconds=duration)
        _transition(uow, run, ReviewRunStatus.COMPLETED, now)
    except Exception as exc:  # noqa: BLE001 — любой сбой шага переводит прогон в failed, а не роняет воркер
        failed = replace(advance(run, ReviewRunStatus.FAILED, now).unwrap(), failure_reason=str(exc))
        with uow:
            uow.review_runs.update(failed)
            uow.commit()
