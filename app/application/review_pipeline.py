"""Оркестрация одного прогона ревью: queued → ... → completed | failed.

Use case уровня application: знает порты (`UnitOfWork`, `LlmGateway`,
`VcsGateway` и порты сборки контекста) и
чистые функции домена (`advance`, отсев замечаний), не знает ни одного
адаптера. Анализ - два прохода модели (спека `review-verification`): ревью,
детерминированный отсев, верификатор, который может только отбросить
замечание. Сбой верификатора прогон не роняет (fail-open). Каждый
переход — свой вход в `with uow:` и свой `commit`, поэтому прогресс,
записанный до сбоя, не откатывается вместе с сбойным шагом: если воркер
упадёт после `analysing`, база уже знает об этом, и `find_stale` сможет
когда-нибудь освободить коммит.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.application.context_assembly import ContextLimits
from app.application.llm_review import LlmLimits, request_review, request_verification
from app.application.ports import UnitOfWork
from app.application.ports.cache_store import CacheStore
from app.application.ports.definition_extractor import DefinitionExtractor
from app.application.ports.llm_gateway import LlmGateway
from app.application.ports.vcs_gateway import VcsGateway
from app.application.publish_review import publish_review
from app.application.use_cases.build_review_context import build_review_context
from app.domain.entities import ContextPayload, Finding, ReviewJob, ReviewRun
from app.domain.enums import (
    TERMINAL_STATUSES,
    ReviewRunStatus,
    VerificationOutcome,
    VerifierVerdict,
)
from app.domain.lifecycle import advance
from app.domain.llm_errors import LlmError
from app.domain.prompt import PromptTexts, candidate_ids
from app.domain.verification import apply_verdicts, screen_findings

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
    vcs_gateway: VcsGateway,
    definition_extractor: DefinitionExtractor,
    cache_store: CacheStore,
    context_limits: ContextLimits,
    llm_limits: LlmLimits,
    prompt_texts: PromptTexts,
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
    Сбои правил и AST-окружения шагом не считаются — их гасит сборка
    контекста, деградируя пакет (`context-assembly`). Сбой верификатора -
    тоже: публикуются замечания, прошедшие отсев, итог - `unavailable`.
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

        assembled = build_review_context(
            job,
            vcs=vcs_gateway,
            extractor=definition_extractor,
            cache=cache_store,
            limits=context_limits,
            review_run_id=run.id,
            now=now(),
            new_id=new_id,
        )
        context = assembled.payload
        with uow:
            uow.context_payloads.add(context)
            uow.commit()

        run = _transition(uow, run, ReviewRunStatus.ANALYSING, now())

        result = request_review(context, prompt_texts, llm_gateway, limits=llm_limits)
        screening = screen_findings(
            result.findings,
            hunks=assembled.hunks,
            shown_rules=assembled.rules,
            review_run_id=run.id,
            now=now(),
            new_id=new_id,
        )
        verification = _verify(
            context, prompt_texts, llm_gateway, llm_limits, screening.kept, run.id
        )
        run = replace(
            run,
            last_progress_at=now(),
            rejected_findings=run.rejected_findings + screening.rejected,
            verification=verification.outcome,
            verifier_dropped_findings=verification.dropped,
        )
        with uow:
            for finding in verification.findings:
                uow.findings.add(finding)
            uow.review_runs.update(run)
            uow.commit()

        run = _transition(uow, run, ReviewRunStatus.PUBLISHING, now())

        publish_review(
            job,
            run,
            result,
            uow=uow,
            vcs=vcs_gateway,
            now=now(),
            new_id=new_id,
        )

        run = replace(
            run,
            model=result.model,
            tokens_used=result.tokens_used + verification.tokens_used,
            duration_seconds=monotonic() - started_at,
        )
        _transition(uow, run, ReviewRunStatus.COMPLETED, now())
    except Exception as exc:
        _mark_failed(uow, job.review_run_id, exc, now())
        raise


@dataclass(frozen=True, slots=True)
class _Verification:
    findings: tuple[Finding, ...]
    outcome: VerificationOutcome
    dropped: int
    tokens_used: int


def _verify(
    context: ContextPayload,
    texts: PromptTexts,
    gateway: LlmGateway,
    limits: LlmLimits,
    candidates: Sequence[Finding],
    run_id: UUID,
) -> _Verification:
    """Второй проход: оставить замечания, которые верификатор подтвердил.

    Без кандидатов модель не вызывается. Сбой вызова или ответ, не покрывающий
    кандидатов ровно по разу, - fail-open: кандидаты публикуются как есть, а
    итог `unavailable` виден в прогоне. Прочие исключения не гасятся.
    """
    if not candidates:
        return _Verification((), VerificationOutcome.NOT_REQUIRED, 0, 0)
    try:
        answer = request_verification(context, texts, gateway, candidates, limits=limits)
    except LlmError:
        logger.warning(
            "верификатор недоступен для прогона %s, публикуются замечания после отсева",
            run_id,
            exc_info=True,
        )
        return _Verification(tuple(candidates), VerificationOutcome.UNAVAILABLE, 0, 0)
    if not answer.verdicts.ok:
        logger.warning(
            "ответ верификатора для прогона %s не принят: %s", run_id, answer.verdicts.error
        )
        return _Verification(
            tuple(candidates), VerificationOutcome.UNAVAILABLE, 0, answer.tokens_used
        )
    verdicts = answer.verdicts.unwrap()
    for candidate_id, finding in zip(candidate_ids(len(candidates)), candidates, strict=True):
        if verdicts[candidate_id].verdict is VerifierVerdict.DROP:
            logger.info(
                "прогон %s: верификатор отбросил замечание %s:%s (%s): %s",
                run_id,
                finding.anchor.file_path,
                finding.anchor.new_line or finding.anchor.old_line,
                finding.category,
                verdicts[candidate_id].reason,
            )
    kept, dropped = apply_verdicts(candidates, verdicts)
    return _Verification(kept, VerificationOutcome.VERIFIED, dropped, answer.tokens_used)
