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

Ход прогона мельче статуса: начало и исход каждого этапа (`fetching_diff`,
`building_context`, `analysing`, `verifying`, `publishing`) пишет
`StepRecorder`, повтор вызова модели — отдельным шагом.
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
from app.application.ports.llm_gateway import LlmGateway, RetryListener
from app.application.ports.vcs_gateway import VcsGateway
from app.application.publish_review import publish_review
from app.application.review_run_steps import (
    StepRecorder,
    close_abandoned_steps,
    spent_tokens,
)
from app.application.use_cases.build_review_context import (
    build_review_context,
    fetch_review_diff,
)
from app.domain.entities import ContextPayload, Finding, ReviewJob, ReviewRun
from app.domain.enums import (
    TERMINAL_STATUSES,
    ReviewRunStage,
    ReviewRunStatus,
    VerificationOutcome,
)
from app.domain.lifecycle import advance
from app.domain.llm_errors import LlmError, LlmOutputError
from app.domain.prompt import PromptTexts
from app.domain.run_steps import error_code
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
    исключение - зависший прогон выметет `sweep_stale_runs`. Токены, которые
    успел израсходовать сбойный вызов модели, добавляются к затратам прогона.
    Шаги, оставшиеся `running`, закрываются следом (`close_abandoned_steps`).
    """
    spent = exc.tokens_used if isinstance(exc, LlmError) else 0
    try:
        with uow:
            run = uow.review_runs.get(run_id)
            if run is None or run.status in TERMINAL_STATUSES:
                return
            failed = replace(
                advance(run, ReviewRunStatus.FAILED, now).unwrap(),
                failure_reason=str(exc) or type(exc).__name__,
            )
            if spent:
                failed = replace(failed, tokens_used=(failed.tokens_used or 0) + spent)
            uow.review_runs.update(failed)
            uow.commit()
    except Exception:
        logger.exception("не удалось перевести прогон %s в failed", run_id)
        return
    close_abandoned_steps(uow, run_id, now=now)


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
    разность монотонных часов, которую тест проверяет точно. Длительность
    шага — та же разность на его границах.

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
    steps = StepRecorder(uow, run.id, now=now, monotonic=monotonic, new_id=new_id)

    try:
        run = _transition(uow, run, ReviewRunStatus.BUILDING_CONTEXT, now())

        steps.start(ReviewRunStage.FETCHING_DIFF)
        diff = fetch_review_diff(job, vcs_gateway)
        steps.finish()

        steps.start(ReviewRunStage.BUILDING_CONTEXT)
        assembled = build_review_context(
            job,
            diff,
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
        steps.finish()

        run = _transition(uow, run, ReviewRunStatus.ANALYSING, now())

        steps.start(ReviewRunStage.ANALYSING)
        result = request_review(
            context, prompt_texts, llm_gateway, limits=llm_limits, on_retry=steps.retry
        )
        # Отсев - часть этапа `analysing`: он разбирает ответ модели. Сбой
        # после ответа закрывает шаг с уже известными токенами - иначе их
        # не узнать: исключение отсева токенов не несёт.
        try:
            screening = screen_findings(
                result.findings,
                hunks=assembled.hunks,
                shown_rules=assembled.rules,
                review_run_id=run.id,
                now=now(),
                new_id=new_id,
            )
        except Exception as exc:
            steps.finish(tokens_used=result.tokens_used, error_code=error_code(exc))
            raise
        steps.finish(tokens_used=result.tokens_used)
        # Два долгих вызова модели подряд не должны выглядеть для `find_stale`
        # одним прогоном без прогресса.
        moment = now()
        run = _record_progress(uow, run, moment)
        steps.start(ReviewRunStage.VERIFYING, at=moment)
        verification = _verify(
            context,
            prompt_texts,
            llm_gateway,
            llm_limits,
            screening.kept,
            run.id,
            on_retry=steps.retry,
        )
        steps.finish(
            tokens_used=verification.step_tokens, error_code=verification.error_code
        )
        if result.findings and not verification.findings:
            # Сводка модели описывает замечания, которых в публикации не будет:
            # публикация возьмёт стандартную.
            result = replace(result, summary=None)
        # Модель и затраты - вместе с замечаниями: сбой публикации их не стирает.
        run = replace(
            run,
            last_progress_at=now(),
            rejected_findings=run.rejected_findings + screening.rejected,
            verification=verification.outcome,
            verifier_dropped_findings=verification.dropped,
            model=result.model,
            tokens_used=result.tokens_used + verification.tokens_used,
        )
        with uow:
            for finding in verification.findings:
                uow.findings.add(finding)
            uow.review_runs.update(run)
            uow.commit()

        run = _transition(uow, run, ReviewRunStatus.PUBLISHING, now())

        steps.start(ReviewRunStage.PUBLISHING)
        publish_review(
            job,
            run,
            result,
            uow=uow,
            vcs=vcs_gateway,
            now=now(),
            new_id=new_id,
        )
        steps.finish()

        run = replace(run, duration_seconds=monotonic() - started_at)
        _transition(uow, run, ReviewRunStatus.COMPLETED, now())
    except Exception as exc:
        steps.fail(exc)
        _mark_failed(uow, job.review_run_id, exc, now())
        raise


@dataclass(frozen=True, slots=True)
class _Verification:
    findings: tuple[Finding, ...]
    outcome: VerificationOutcome
    dropped: int
    tokens_used: int
    # Для шага `verifying`: токены, если модель вызывалась, и код сбоя, если
    # второй проход не удался (прогон при этом не падает).
    step_tokens: int | None = None
    error_code: str | None = None


def _verify(
    context: ContextPayload,
    texts: PromptTexts,
    gateway: LlmGateway,
    limits: LlmLimits,
    candidates: Sequence[Finding],
    run_id: UUID,
    *,
    on_retry: RetryListener | None = None,
) -> _Verification:
    """Второй проход: оставить замечания, которые верификатор подтвердил.

    Без кандидатов модель не вызывается. Сбой вызова или ответ, не покрывающий
    кандидатов ровно по разу, - fail-open: кандидаты публикуются как есть, а
    итог `unavailable` виден в прогоне. Прочие исключения не гасятся.
    """
    if not candidates:
        return _Verification((), VerificationOutcome.NOT_REQUIRED, 0, 0)
    try:
        answer = request_verification(
            context, texts, gateway, candidates, limits=limits, on_retry=on_retry
        )
    except LlmError as error:
        logger.warning(
            "верификатор недоступен для прогона %s, публикуются замечания после отсева",
            run_id,
            exc_info=True,
        )
        return _Verification(
            tuple(candidates),
            VerificationOutcome.UNAVAILABLE,
            0,
            error.tokens_used,
            step_tokens=spent_tokens(error),
            error_code=error.code,
        )
    if not answer.verdicts.ok:
        logger.warning(
            "ответ верификатора для прогона %s не принят: %s", run_id, answer.verdicts.error
        )
        return _Verification(
            tuple(candidates),
            VerificationOutcome.UNAVAILABLE,
            0,
            answer.tokens_used,
            step_tokens=answer.tokens_used,
            error_code=LlmOutputError.code,
        )
    applied = apply_verdicts(candidates, answer.verdicts.unwrap())
    for finding, reason in applied.dropped:
        logger.info(
            "прогон %s: верификатор отбросил замечание %s (%s/%s): %s",
            run_id,
            _where(finding),
            finding.category,
            finding.severity,
            reason,
        )
    for finding, reason in applied.overruled:
        logger.warning(
            "прогон %s: верификатор хотел отбросить замечание %s (%s/%s), оно оставлено: %s",
            run_id,
            _where(finding),
            finding.category,
            finding.severity,
            reason,
        )
    return _Verification(
        applied.kept,
        VerificationOutcome.VERIFIED,
        len(applied.dropped),
        answer.tokens_used,
        step_tokens=answer.tokens_used,
    )


def _where(finding: Finding) -> str:
    return f"{finding.anchor.file_path}:{finding.anchor.new_line or finding.anchor.old_line}"


def _record_progress(uow: UnitOfWork, run: ReviewRun, now: datetime) -> ReviewRun:
    """Записать `last_progress_at` без смены состояния."""
    run = replace(run, last_progress_at=now)
    with uow:
        uow.review_runs.update(run)
        uow.commit()
    return run
