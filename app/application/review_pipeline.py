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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.application.context_assembly import ContextLimits
from app.application.llm_review import LlmLimits, request_review, request_verification
from app.application.merge_gate_publish import (
    build_failed_review_check,
    head_run_in_charge,
    pause_check,
    publish_pause_check,
    settle_merge_gate,
)
from app.application.ports import UnitOfWork
from app.application.ports.cache_store import CacheStore
from app.application.ports.definition_extractor import DefinitionExtractor
from app.application.ports.llm_gateway import LlmGateway, LlmMeter, RetryListener
from app.application.ports.run_outcome import NO_RUN_OUTCOME, RunOutcomeRecorder
from app.application.ports.stage_tracer import NO_STAGE_TRACING, StageTracer
from app.application.ports.unit_of_work import RunStateConflict
from app.application.ports.vcs_gateway import VcsGateway
from app.application.publish_review import publish_review
from app.application.review_run_steps import (
    StepRecorder,
    called_model,
    close_abandoned_steps,
)
from app.application.use_cases.build_review_context import (
    build_review_context,
    fetch_review_diff,
)
from app.domain.entities import (
    ContextPayload,
    Finding,
    ReviewJob,
    ReviewRun,
    ReviewRunStep,
)
from app.domain.enums import (
    TERMINAL_STATUSES,
    ReviewRunStage,
    ReviewRunStatus,
    VerificationOutcome,
)
from app.domain.lifecycle import advance
from app.domain.llm_errors import LlmError, LlmOutputError
from app.domain.llm_usage import NO_USAGE, LlmUsage
from app.domain.prompt import PromptTexts
from app.domain.run_steps import error_code
from app.domain.vcs_errors import VcsTransientError
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


def _claim(uow: UnitOfWork, run: ReviewRun, now: datetime) -> ReviewRun | None:
    """Захватить прогон из `queued`; `None` — его уже изменил кто-то другой."""
    claimed = advance(run, ReviewRunStatus.BUILDING_CONTEXT, now).unwrap()
    try:
        with uow:
            uow.review_runs.update(claimed, previous=run)
            uow.commit()
    except RunStateConflict:
        return None
    return claimed


def _mark_failed(
    uow: UnitOfWork,
    run_id: UUID,
    exc: Exception,
    now: datetime,
    *,
    run_outcome: RunOutcomeRecorder = NO_RUN_OUTCOME,
    usage: LlmUsage = NO_USAGE,
    unwritten_steps: Mapping[UUID, ReviewRunStep] | None = None,
) -> None:
    """Перевести прогон в failed по свежей строке из базы.

    Копия прогона в памяти могла разойтись с базой (строка ушла вперёд), и
    переход от неё бросил бы сам. Терминальный прогон не трогаем. Сбой этой
    записи (например, база недоступна) логируется и не подменяет исходное
    исключение - зависший прогон выметет `sweep_stale_runs`. Затраты прогона -
    `usage`, итог счётчика модели к сбою: он включает и уже записанное, поэтому
    ставится, а не прибавляется; без расхода затраты не трогаются.
    Шаги, оставшиеся `running`, закрываются следом (`close_abandoned_steps`):
    шаг из `unwritten_steps` - своим незаписанным исходом, прочие -
    `REVIEW_RUN_STALE`. Запись в счётчик исходов — только за фактический
    терминальный переход: ранний выход (прогона нет, он уже терминален) и
    сбой записи прогоном не считаются.
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
            if usage != NO_USAGE:
                failed = replace(failed, tokens_used=usage.total_tokens, usage=usage)
            uow.review_runs.update(failed)
            uow.commit()
    except Exception:
        logger.exception("не удалось перевести прогон в failed")
        return
    run_outcome.run_finished(ReviewRunStatus.FAILED.value)
    close_abandoned_steps(uow, run_id, now=now, known=unwritten_steps)


def _record_cancelled_spend(uow: UnitOfWork, run_id: UUID, usage: LlmUsage) -> None:
    """Дописать отменённому прогону расход модели: провайдер его уже списал.

    Best-effort, как `_mark_failed`: сбой записи - в лог, ход отмены не меняется.
    Ставится итог счётчика, а не прибавка: он включает и уже записанное.
    """
    if usage == NO_USAGE:
        return
    try:
        with uow:
            uow.review_runs.record_cancelled_spend(run_id, usage)
            uow.commit()
    except Exception:
        logger.exception("не удалось записать расход отменённого прогона")


def _was_cancelled(uow: UnitOfWork, run_id: UUID) -> bool:
    """Отменён ли прогон по свежей строке из базы. Сбой чтения - `False`: исходное
    исключение тогда уходит наружу, как и без проверки."""
    try:
        with uow:
            run = uow.review_runs.get(run_id)
    except Exception:
        logger.exception("не удалось перечитать статус прогона %s", run_id)
        return False
    return run is not None and run.status is ReviewRunStatus.CANCELLED


def block_head_of_failed_run(
    uow: UnitOfWork,
    vcs: VcsGateway,
    *,
    run_id: UUID,
    head_sha: str,
    repository_full_name: str,
    installation_id: int,
) -> bool:
    """Выставить `failure` на head, если упавший прогон на нём последний, а до него
    на этот head уже было ревью (ревью #225), или check паузы, если ревью на паузе.

    Иначе на head мог остаться `success` прежнего прогона по старому диффу: его
    поправку после смены ветки (`settle_merge_gate`) GitHub не принял. Без
    прежнего ревью check run на head нет, обязательная проверка и так не даёт
    мерджа, и упавший прогон на PR ничего не оставляет. Прогон, за которым на head
    уже есть новый, gate не трогает - head отвечает тот. На паузе check паузы ставится
    и без прежнего ревью: иначе обязательная проверка держала бы PR, который ревью не
    ждёт. Исключение - пауза авторевью, когда последний на head - упавший повтор ручного
    ревью, а до него есть завершённый ручной прогон: за head снова отвечает он
    (`head_run_in_charge`), и выставляется его gate. Зовут пайплайн и очистка
    (`sweep_stale_runs`). Best-effort, как `_mark_failed`: сбой пишется в лог и не подменяет исходное
    исключение. `False` - GitHub временно недоступен (`VcsTransientError`): очистке дальше в
    проходе к нему не ходить; воркер результат не читает.
    """
    try:
        with uow:
            run = uow.review_runs.get(run_id)
            if run is None or run.status is not ReviewRunStatus.FAILED:
                return True
            latest = uow.review_runs.find_latest(run.merge_request_id, head_sha)
            earlier = uow.review_runs.find_reviewed(run.merge_request_id, head_sha)
            merge_request = uow.merge_requests.get(run.merge_request_id)
            repository = uow.repositories.get(merge_request.repository_id) if merge_request else None
            # Вытеснен ли прогон - по `latest`; чей gate на head - по `in_charge`.
            in_charge = head_run_in_charge(uow, repository, run.merge_request_id, head_sha)
        if latest is None or latest.id != run.id or repository is None or merge_request is None:
            return True
        pause = pause_check(repository, in_charge, merge_request.target_branch)
        if pause is None and in_charge is not None and in_charge.id != run.id:
            # Пауза авторевью или фильтр, повтор ручного ревью упал: gate завершённого ручного.
            settle_merge_gate(
                uow,
                vcs,
                merge_request_id=run.merge_request_id,
                repo_full_name=repository_full_name,
                head_sha=head_sha,
                installation_id=installation_id,
                run_id=in_charge.id,
                repository_id=repository.id,
            )
            return True
        if pause is not None:
            # Пока идёт POST, за head мог стать новый ручной прогон и выставить свой gate.
            publish_pause_check(
                uow,
                vcs,
                pause,
                repository_id=repository.id,
                pr_number=merge_request.number,
                repo_full_name=repository_full_name,
                head_sha=head_sha,
                installation_id=installation_id,
            )
            return True
        check = build_failed_review_check() if earlier is not None else None
        if check is not None:
            vcs.set_merge_gate_check(repository_full_name, head_sha, installation_id, check)
    except VcsTransientError:
        logger.exception(
            "прогон упал, а check run merge gate на %s не выставлен: GitHub недоступен",
            head_sha,
            extra={"review_run_id": str(run_id)},
        )
        return False
    except Exception:
        logger.exception(
            "прогон упал, а check run merge gate на %s не выставлен",
            head_sha,
            extra={"review_run_id": str(run_id)},
        )
    return True


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
    stage_tracer: StageTracer = NO_STAGE_TRACING,
    run_outcome: RunOutcomeRecorder = NO_RUN_OUTCOME,
) -> None:
    """Довести прогон до терминального состояния.

    Часы приходят функциями, как `new_id`: каждый переход берёт свой `now()`,
    поэтому `last_progress_at` отражает реальный прогресс (по нему
    `find_stale` отличает зависший прогон от идущего), а `duration_seconds` —
    разность монотонных часов, которую тест проверяет точно. Длительность
    шага — та же разность на его границах.
    `stage_tracer` открывает спан трассировки на каждый шаг; это телеметрия,
    результат прогона от неё не зависит. `run_outcome` пишет исход прогона в
    бизнес-метрики на фактическом терминальном переходе — телеметрия, на
    результат которой прогон тоже не влияет.

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
    if run.status is not ReviewRunStatus.QUEUED:
        # Чужой прогон в работе: живой ли его воркер, отсюда не узнать. Перехвата нет:
        # на `publishing` он дал бы второе ревью. Брошенный выметет очистка (#169).
        logger.warning("прогон уже в работе (%s), сообщение подтверждается", run.status)
        return

    started_at = monotonic()
    # Расход всех вызовов модели прогона: адаптер пишет сюда каждый ответ
    # провайдера, шаги берут из него свои доли, прогон - итог.
    meter = LlmMeter()
    steps = StepRecorder(
        uow, run.id, now=now, monotonic=monotonic, new_id=new_id, meter=meter, tracer=stage_tracer
    )

    try:
        claimed = _claim(uow, run, now())
        if claimed is None:
            logger.warning("прогон изменён другим, сообщение подтверждается")
            return
        run = claimed

        steps.start(ReviewRunStage.FETCHING_DIFF)
        diff = fetch_review_diff(job, vcs_gateway, max_bytes=context_limits.diff_max_bytes)
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

        # Каждая запись прогона - внутри открытого шага: её сбой падает в шаг
        # с кодом, а не оставляет прогон `failed` при одних `completed`-шагах.
        steps.start(ReviewRunStage.ANALYSING)
        run = _transition(uow, run, ReviewRunStatus.ANALYSING, now())
        result = request_review(
            context,
            prompt_texts,
            llm_gateway,
            limits=llm_limits,
            on_retry=steps.retry,
            meter=meter,
        )
        # Отсев - часть этапа `analysing`: он разбирает ответ модели. Сбой
        # после ответа закрывает шаг с его токенами: исключение отсева - не
        # сбой модели, и `fail` счёл бы шаг не вызывавшим модель.
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
            steps.finish(used_model=True, error_code=error_code(exc))
            raise
        steps.finish(used_model=True)
        # Два долгих вызова модели подряд не должны выглядеть для `find_stale`
        # одним прогоном без прогресса.
        moment = now()
        steps.start(ReviewRunStage.VERIFYING, at=moment)
        run = _record_progress(uow, run, moment)
        verification = _verify(
            context,
            prompt_texts,
            llm_gateway,
            llm_limits,
            screening.kept,
            run.id,
            on_retry=steps.retry,
            meter=meter,
        )
        steps.finish(used_model=verification.called_model, error_code=verification.error_code)
        if result.findings and not verification.findings:
            # Сводка модели описывает замечания, которых в публикации не будет:
            # публикация возьмёт стандартную.
            result = replace(result, summary=None)
        # Запись замечаний и переход - уже публикация: их сбой - шаг `publishing`.
        steps.start(ReviewRunStage.PUBLISHING)
        # Модель и затраты - вместе с замечаниями: сбой публикации их не стирает.
        run = replace(
            run,
            last_progress_at=now(),
            rejected_findings=run.rejected_findings + screening.rejected,
            verification=verification.outcome,
            verifier_dropped_findings=verification.dropped,
            model=result.model,
            tokens_used=meter.total.total_tokens,
            usage=meter.total,
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
            coverage=assembled.coverage,
            uow=uow,
            vcs=vcs_gateway,
            now=now(),
            new_id=new_id,
        )
        steps.finish()

        run = replace(run, duration_seconds=monotonic() - started_at)
        moment = now()
        _transition(uow, run, ReviewRunStatus.COMPLETED, moment)
        run_outcome.run_finished(ReviewRunStatus.COMPLETED.value)
        # Шаг, чей исход не записался, иначе остался бы `running` у завершённого прогона.
        close_abandoned_steps(uow, run.id, now=moment, known=steps.unwritten)
    except Exception as exc:
        if _was_cancelled(uow, job.review_run_id):
            # Ретаргет отменил прогон посреди ревью (`supersede`): переход бросил на
            # терминальном статусе или публикация отказалась. Это не сбой - открытый
            # шаг брошен, сообщение уходит в ack, а не в DLQ, head отвечает новый прогон.
            steps.abandon()
            _record_cancelled_spend(uow, job.review_run_id, meter.total)
            logger.info("прогон %s отменён во время ревью, результат отброшен", job.review_run_id)
            close_abandoned_steps(uow, job.review_run_id, now=now(), known=steps.unwritten)
            return
        steps.fail(exc)
        _mark_failed(
            uow,
            job.review_run_id,
            exc,
            now(),
            run_outcome=run_outcome,
            usage=meter.total,
            unwritten_steps=steps.unwritten,
        )
        block_head_of_failed_run(
            uow,
            vcs_gateway,
            run_id=job.review_run_id,
            head_sha=job.head_sha,
            repository_full_name=job.repository_full_name,
            installation_id=job.installation_id,
        )
        raise
    except BaseException as exc:
        steps.interrupt(exc)
        raise


@dataclass(frozen=True, slots=True)
class _Verification:
    findings: tuple[Finding, ...]
    outcome: VerificationOutcome
    dropped: int
    # Для шага `verifying`: вызывалась ли модель и код сбоя, если второй
    # проход не удался (прогон при этом не падает).
    called_model: bool = False
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
    meter: LlmMeter | None = None,
) -> _Verification:
    """Второй проход: оставить замечания, которые верификатор подтвердил.

    Без кандидатов модель не вызывается. Сбой вызова или ответ, не покрывающий
    кандидатов ровно по разу, - fail-open: кандидаты публикуются как есть, а
    итог `unavailable` виден в прогоне. Прочие исключения не гасятся.
    """
    if not candidates:
        return _Verification((), VerificationOutcome.NOT_REQUIRED, 0)
    try:
        verdicts = request_verification(
            context, texts, gateway, candidates, limits=limits, on_retry=on_retry, meter=meter
        )
    except LlmError as error:
        # Идентификатор прогона — атрибутом из контекста логов, не в тексте.
        logger.warning(
            "верификатор недоступен, публикуются замечания после отсева",
            exc_info=True,
        )
        return _Verification(
            tuple(candidates),
            VerificationOutcome.UNAVAILABLE,
            0,
            called_model=called_model(error),
            error_code=error.code,
        )
    if not verdicts.ok:
        logger.warning("ответ верификатора не принят: %s", verdicts.error)
        return _Verification(
            tuple(candidates),
            VerificationOutcome.UNAVAILABLE,
            0,
            called_model=True,
            error_code=LlmOutputError.code,
        )
    applied = apply_verdicts(candidates, verdicts.unwrap())
    for finding, reason in applied.dropped:
        logger.info(
            "верификатор отбросил замечание %s (%s/%s): %s",
            _where(finding),
            finding.category,
            finding.severity,
            reason,
        )
    for finding, reason in applied.overruled:
        logger.warning(
            "верификатор хотел отбросить замечание %s (%s/%s), оно оставлено: %s",
            _where(finding),
            finding.category,
            finding.severity,
            reason,
        )
    return _Verification(
        applied.kept,
        VerificationOutcome.VERIFIED,
        len(applied.dropped),
        called_model=True,
    )


def _where(finding: Finding) -> str:
    return f"{finding.anchor.file_path}:{finding.anchor.line}"


def _record_progress(uow: UnitOfWork, run: ReviewRun, now: datetime) -> ReviewRun:
    """Записать `last_progress_at` без смены состояния."""
    run = replace(run, last_progress_at=now)
    with uow:
        uow.review_runs.update(run)
        uow.commit()
    return run
