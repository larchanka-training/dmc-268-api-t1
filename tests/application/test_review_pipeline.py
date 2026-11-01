"""Оркестрация одного прогона ревью — фейковый `UnitOfWork`, без БД и брокера."""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any, NoReturn
from uuid import UUID

import pytest
from pydantic import BaseModel

from app.application.context_assembly import ContextLimits
from app.application.llm_review import LlmLimits
from app.application.merge_gate_publish import build_failed_review_check
from app.application.ports.llm_gateway import LlmRequest, LlmResponse, LlmTask
from app.application.review_pipeline import run_review
from app.domain.entities import (
    MergeRequest,
    ReviewJob,
    ReviewRun,
    ReviewRunStep,
    RuleRef,
)
from app.domain.enums import (
    MergeRequestState,
    ReviewRunStage,
    ReviewRunStatus,
    ReviewRunStepStatus,
    TriggerSource,
    VerificationOutcome,
)
from app.domain.llm_errors import (
    LlmError,
    LlmOutputError,
    LlmRateLimitedError,
    LlmTimeoutError,
    PromptTooLargeError,
)
from app.domain.prompt import PromptTexts
from app.domain.run_steps import start_step
from app.domain.vcs_errors import DiffTooLargeError, VcsError, VcsTransientError
from app.infrastructure.llm.fallback import FallbackLlmGateway
from app.infrastructure.vcs.errors import VcsNotFoundError

from ..fakes.context import FakeCache, FakeExtractor
from ..fakes.llm_gateway import (
    ScriptedLlmGateway,
    finding_json,
    review_response,
    verification_response,
)
from ..fakes.run_outcome import RecordingRunOutcome
from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.vcs_gateway import RecordingVcsGateway
from ..fakes.webhooks import FakeVcs, a_repository

# Дифф фейкового VCS меняет первую строку `app/main.py`.
CHANGED_FILE = "app/main.py"
LIMITS = ContextLimits(32_000, 4_000, 32_768, 60, 20, 262_144, 1_000, diff_max_bytes=5_242_880)
LLM_LIMITS = LlmLimits(window_tokens=65_536, max_output_tokens=4_096)
TEXTS = PromptTexts(review_system="ревью", verifier_system="верификация")

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    event_type="pull_request",
    action="opened",
    installation_id=512804923,
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)


MR_ID = UUID(int=3)


def _seed_run(uow: FakeUnitOfWork, *, status: ReviewRunStatus = ReviewRunStatus.QUEUED) -> None:
    # Запись репозитория PR: без неё merge gate не прочитает порог.
    uow.repositories.add(replace(a_repository(), id=UUID(int=9)))
    uow.merge_requests.add(
        MergeRequest(
            id=MR_ID,
            repository_id=UUID(int=9),
            number=JOB.pull_request_number,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="develop",
            head_sha=JOB.head_sha,
            state=MergeRequestState.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.review_runs.add(
        ReviewRun(
            id=JOB.review_run_id,
            merge_request_id=MR_ID,
            head_sha=JOB.head_sha,
            status=status,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )


class _Clock:
    """Часы, идущие на секунду при каждом чтении: переходы различимы по времени."""

    def __init__(self) -> None:
        self.reads = 0

    def now(self) -> datetime:
        self.reads += 1
        return NOW + timedelta(seconds=self.reads)


class _Monotonic:
    """Монотонные часы, идущие на `step` секунд при каждом чтении; показания записываются."""

    def __init__(self, step: float = 0.5) -> None:
        self._step = step
        self.readings: list[float] = []

    def __call__(self) -> float:
        reading = len(self.readings) * self._step
        self.readings.append(reading)
        return reading


def _run(uow: FakeUnitOfWork, gateway: object, **overrides: object) -> None:
    kwargs: dict[str, object] = {
        "uow": uow,
        "llm_gateway": gateway,
        "vcs_gateway": RecordingVcsGateway(),
        "definition_extractor": FakeExtractor(),
        "cache_store": FakeCache(),
        "context_limits": LIMITS,
        "llm_limits": LLM_LIMITS,
        "prompt_texts": TEXTS,
        "now": _Clock().now,
        "monotonic": _Monotonic(),
        "new_id": _new_id(),
    }
    kwargs.update(overrides)
    run_review(JOB, **kwargs)  # type: ignore[arg-type]


def _new_id() -> Callable[[], UUID]:
    counter = count(100)
    return lambda: UUID(int=next(counter))


def test_successful_run_reaches_completed_with_model_and_tokens() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway([review_response(model="stub-llm")])
    monotonic = _Monotonic()

    _run(uow, gateway, monotonic=monotonic)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.model == "stub-llm"
    assert run.tokens_used == 0
    # Первое показание - начало прогона, последнее - его конец.
    assert run.duration_seconds == monotonic.readings[-1] - monotonic.readings[0]
    assert uow.context_payloads.list_for_run(JOB.review_run_id)


def test_context_is_built_from_the_real_diff() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    _run(uow, ScriptedLlmGateway([review_response()]))

    (payload,) = uow.context_payloads.list_for_run(JOB.review_run_id)
    assert payload.file_paths == (CHANGED_FILE,)
    assert payload.body["schema"] == "context-payload/v1"
    assert "+new" in payload.body["diff"][0]["hunk"]


# --- счётчик исходов: терминальный переход, а не обработка сообщения -----------------


def test_a_completed_run_is_counted_once() -> None:
    """Доведённый до терминального статуса прогон — ровно одна запись `completed`."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    recorder = RecordingRunOutcome()

    _run(uow, ScriptedLlmGateway([review_response()]), run_outcome=recorder)

    assert recorder.statuses == ["completed"]


def test_a_failed_run_is_counted_once() -> None:
    """Упавший прогон — ровно одна запись `failed`, даже при повторных коммитах."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    recorder = RecordingRunOutcome()

    with pytest.raises(LlmTimeoutError):
        _run(
            uow,
            ScriptedLlmGateway([LlmTimeoutError("таймаут", tokens_used=1)]),
            run_outcome=recorder,
        )

    assert recorder.statuses == ["failed"]
    assert uow.review_runs.get(JOB.review_run_id).status is ReviewRunStatus.FAILED  # type: ignore[union-attr]


def test_a_failed_translation_is_not_counted() -> None:
    """Сбой записи перевода в failed — записи в счётчике нет: прогон ещё не терминален."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    uow.commit_error = RuntimeError("база недоступна")
    recorder = RecordingRunOutcome()

    with pytest.raises(RuntimeError, match="база недоступна"):
        _run(uow, ScriptedLlmGateway([review_response()]), run_outcome=recorder)

    assert recorder.statuses == []


def test_a_redelivered_terminal_run_is_not_counted() -> None:
    """Повторная доставка терминального прогона возвращается молча — счётчик не растёт."""
    uow = FakeUnitOfWork()
    _seed_run(uow, status=ReviewRunStatus.COMPLETED)
    recorder = RecordingRunOutcome()

    _run(uow, ScriptedLlmGateway([review_response()]), run_outcome=recorder)

    assert recorder.statuses == []


def test_a_message_without_a_run_is_not_counted() -> None:
    """Прогона нет (`LookupError`) — считать нечего: это не исход прогона."""
    uow = FakeUnitOfWork()
    recorder = RecordingRunOutcome()

    with pytest.raises(LookupError):
        _run(uow, ScriptedLlmGateway([review_response()]), run_outcome=recorder)

    assert recorder.statuses == []


def test_diff_failure_marks_the_run_failed_and_reraises() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway([review_response()])

    with pytest.raises(VcsError, match="GitHub недоступен"):
        _run(uow, gateway, vcs_gateway=FakeVcs(error=VcsError("GitHub недоступен")))

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason == "GitHub недоступен"


def test_rules_failure_does_not_fail_the_run() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    vcs = FakeVcs(file_errors={".review/rules.md": VcsError("403")})

    _run(uow, ScriptedLlmGateway([review_response()]), vcs_gateway=vcs)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    (payload,) = uow.context_payloads.list_for_run(JOB.review_run_id)
    assert payload.body["rules"]["status"] == "unavailable"


def test_a_finding_anchored_in_the_diff_is_persisted() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "находка в диффе"), tokens=5),
            verification_response(("F1", "keep")),
        ]
    )

    _run(uow, gateway)

    findings = uow.findings.list_for_run(JOB.review_run_id)
    assert len(findings) == 1
    assert findings[0].message == "находка в диффе"


def test_llm_failure_marks_the_run_failed_with_a_reason_and_reraises() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    with pytest.raises(RuntimeError, match="модель недоступна"):
        _run(uow, ScriptedLlmGateway([RuntimeError("модель недоступна")]))

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason == "модель недоступна"
    assert run.failed_stage is ReviewRunStatus.ANALYSING


def test_missing_run_raises_so_the_message_goes_to_the_dlq() -> None:
    uow = FakeUnitOfWork()
    gateway = ScriptedLlmGateway([review_response()])

    with pytest.raises(LookupError):
        _run(uow, gateway)

    assert uow.review_runs.get(JOB.review_run_id) is None


def test_last_progress_at_follows_the_clock_on_every_transition() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    seen: list[datetime] = []
    original = uow.review_runs.update

    def recording_update(run: ReviewRun) -> None:
        seen.append(run.last_progress_at)
        original(run)

    uow.review_runs.update = recording_update  # type: ignore[method-assign]
    gateway = ScriptedLlmGateway([review_response()])

    _run(uow, gateway)

    # building_context, analysing, отметка после ревью, итог анализа, publishing, completed
    assert len(seen) == 6
    assert seen == sorted(seen)
    assert len(set(seen)) == 6


def test_progress_is_recorded_between_the_review_and_the_verifier() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    clock = _Clock()
    scripted = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "находка")),
            verification_response(("F1", "keep")),
        ]
    )
    progress_at_call: list[datetime] = []
    reads_at_call: list[int] = []

    class _Watching:
        def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
            progress_at_call.append(_completed(uow).last_progress_at)
            reads_at_call.append(clock.reads)
            return scripted.complete(request)

    _run(uow, _Watching(), now=clock.now)

    at_review, at_verify = progress_at_call
    # Отметка перед верификатором - последнее показание часов до его вызова,
    # то есть после ответа на ревью, а не переход в analysing.
    assert at_verify > at_review
    assert at_verify == NOW + timedelta(seconds=reads_at_call[1])


def test_a_rejected_anchor_keeps_the_other_findings_and_the_count() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    gateway = ScriptedLlmGateway(
        [
            review_response(
                finding_json(CHANGED_FILE, 1, "внутри диффа"),
                finding_json("вне/диффа.py", 1, "вне диффа"),
                finding_json(CHANGED_FILE, 1, "тоже внутри", category="security"),
            ),
            verification_response(("F1", "keep"), ("F2", "keep")),
        ]
    )

    _run(uow, gateway)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.rejected_findings == 1
    assert {f.message for f in uow.findings.list_for_run(JOB.review_run_id)} == {
        "внутри диффа",
        "тоже внутри",
    }


def test_a_failure_while_marking_failed_does_not_mask_the_original_error() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    def broken_update(run: ReviewRun) -> None:
        if run.status == ReviewRunStatus.FAILED:
            raise ConnectionError("база недоступна")

    real_update = uow.review_runs.update

    def update(run: ReviewRun) -> None:
        broken_update(run)
        real_update(run)

    uow.review_runs.update = update  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="модель недоступна"):
        _run(uow, ScriptedLlmGateway([RuntimeError("модель недоступна")]))


def test_marking_failed_reads_the_fresh_row_not_the_stale_copy() -> None:
    """Выметатель уже перевёл строку в failed — вторая запись не падает и не затирает причину."""
    uow = FakeUnitOfWork()
    _seed_run(uow)

    class SweptGateway:
        def complete(self, request: object) -> object:
            current = uow.review_runs.get(JOB.review_run_id)
            assert current is not None
            swept = replace(current, status=ReviewRunStatus.FAILED, failure_reason="выметен")
            uow.review_runs.update(swept)
            raise RuntimeError("сбой после выметания")

    with pytest.raises(RuntimeError, match="сбой после выметания"):
        _run(uow, SweptGateway())

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason == "выметен"


def test_already_terminal_run_is_left_untouched() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow, status=ReviewRunStatus.COMPLETED)
    gateway = ScriptedLlmGateway([review_response()])

    _run(uow, gateway)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.model is None


def _completed(uow: FakeUnitOfWork) -> ReviewRun:
    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    return run


def test_review_output_error_fails_the_run_at_analysing_without_findings() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway([LlmOutputError("findings.0.severity: Field required")])

    with pytest.raises(LlmOutputError):
        _run(uow, gateway)

    run = _completed(uow)
    assert run.status is ReviewRunStatus.FAILED
    assert run.failed_stage is ReviewRunStatus.ANALYSING
    assert "severity" in (run.failure_reason or "")
    assert uow.findings.list_for_run(JOB.review_run_id) == []


def test_review_over_the_model_window_fails_the_run_at_analysing() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway([])

    with pytest.raises(PromptTooLargeError):
        _run(uow, gateway, llm_limits=LlmLimits(window_tokens=100, max_output_tokens=50))

    run = _completed(uow)
    assert run.status is ReviewRunStatus.FAILED
    assert run.failed_stage is ReviewRunStatus.ANALYSING
    assert "больше окна 100" in (run.failure_reason or "")
    assert gateway.requests == []


def test_verification_over_the_model_window_is_fail_open() -> None:
    # Запрос ревью мал, а кандидат длиной ~75 тысяч токенов выводит запрос
    # верификатора за окно 65 536.
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway([review_response(finding_json(CHANGED_FILE, 1, "x" * 300_000))])

    _run(uow, gateway)

    run = _completed(uow)
    assert run.status is ReviewRunStatus.COMPLETED
    assert run.verification is VerificationOutcome.UNAVAILABLE
    assert [r.task for r in gateway.requests] == [LlmTask.REVIEW]
    assert len(uow.findings.list_for_run(JOB.review_run_id)) == 1


def test_empty_review_skips_the_verifier() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway([review_response()])

    _run(uow, gateway)

    assert [r.task for r in gateway.requests] == [LlmTask.REVIEW]
    assert _completed(uow).verification is VerificationOutcome.NOT_REQUIRED


def test_verifier_drops_one_of_three_and_the_others_keep_their_text() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(
                finding_json(CHANGED_FILE, 1, "первое", category="security"),
                finding_json(CHANGED_FILE, 1, "второе", category="correctness"),
                finding_json(CHANGED_FILE, 1, "третье", category="performance"),
            ),
            verification_response(("F1", "keep"), ("F2", "drop"), ("F3", "keep")),
        ]
    )

    _run(uow, gateway)

    run = _completed(uow)
    assert run.verification is VerificationOutcome.VERIFIED
    assert run.verifier_dropped_findings == 1
    assert [f.message for f in uow.findings.list_for_run(JOB.review_run_id)] == ["первое", "третье"]


def test_verifier_failure_is_fail_open() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "находка")),
            LlmError("таймаут провайдера"),
        ]
    )

    _run(uow, gateway)

    run = _completed(uow)
    assert run.status is ReviewRunStatus.COMPLETED
    assert run.verification is VerificationOutcome.UNAVAILABLE
    assert run.verifier_dropped_findings == 0
    assert [f.message for f in uow.findings.list_for_run(JOB.review_run_id)] == ["находка"]


def test_verifier_that_skips_a_finding_is_treated_as_unavailable() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(
                finding_json(CHANGED_FILE, 1, "a", category="security"),
                finding_json(CHANGED_FILE, 1, "b", category="correctness"),
                tokens=10,
            ),
            verification_response(("F1", "drop"), tokens=3),
        ]
    )

    _run(uow, gateway)

    run = _completed(uow)
    assert run.verification is VerificationOutcome.UNAVAILABLE
    assert len(uow.findings.list_for_run(JOB.review_run_id)) == 2
    assert run.tokens_used == 13


def test_tokens_of_a_verifier_that_never_answered_by_the_schema_are_counted() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "находка"), tokens=1000),
            LlmOutputError("verdicts: Field required", tokens_used=300),
        ]
    )

    _run(uow, gateway)

    run = _completed(uow)
    assert run.status is ReviewRunStatus.COMPLETED
    assert run.verification is VerificationOutcome.UNAVAILABLE
    assert run.tokens_used == 1300


def test_tokens_of_a_review_that_never_answered_by_the_schema_are_counted() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway([LlmOutputError("findings: Field required", tokens_used=700)])

    with pytest.raises(LlmOutputError):
        _run(uow, gateway)

    run = _completed(uow)
    assert run.status is ReviewRunStatus.FAILED
    assert run.tokens_used == 700


class _FailingPublication(RecordingVcsGateway):
    def publish_pull_request_review(self, *args: object, **kwargs: object) -> NoReturn:
        raise VcsError("GitHub недоступен")


def test_a_publication_failure_keeps_the_model_and_the_tokens() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "x"), tokens=1000, model="qwen"),
            verification_response(("F1", "keep"), tokens=400),
        ]
    )

    with pytest.raises(VcsError):
        _run(uow, gateway, vcs_gateway=_FailingPublication())

    run = _completed(uow)
    assert run.status is ReviewRunStatus.FAILED
    assert run.failed_stage is ReviewRunStatus.PUBLISHING
    assert (run.model, run.tokens_used) == ("qwen", 1400)


class _RetargetDuringPublication(RecordingVcsGateway):
    """Смена целевой ветки приходится на POST ревью: приём переводит прогон в
    `cancelled`, как `_supersede` в `handle_webhook`."""

    def __init__(self, uow: FakeUnitOfWork) -> None:
        super().__init__()
        self._uow = uow

    def publish_pull_request_review(self, *args: Any, **kwargs: Any) -> Any:
        current = self._uow.review_runs.get(JOB.review_run_id)
        assert current is not None
        self._uow.review_runs.update(replace(current, status=ReviewRunStatus.CANCELLED))
        return super().publish_pull_request_review(*args, **kwargs)


def test_a_run_cancelled_by_a_retarget_is_acked_not_dead_lettered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Блокер ревью #225: переход отменённого прогона бросает `cancelled is terminal`.
    Воркер не пробрасывает его - иначе сообщение уходит в DLQ - и не трогает gate:
    head отвечает новый прогон."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    vcs = _RetargetDuringPublication(uow)

    with caplog.at_level(logging.INFO, logger="app.application.review_pipeline"):
        _run(uow, ScriptedLlmGateway([review_response()]), vcs_gateway=vcs)

    run = _completed(uow)
    assert run.status is ReviewRunStatus.CANCELLED
    assert build_failed_review_check() not in vcs.checks
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert all(
        step.status is not ReviewRunStepStatus.RUNNING
        for step in uow.review_run_steps.list_for_run(JOB.review_run_id)
    )


def test_a_run_cancelled_before_its_review_is_posted_leaves_the_step_abandoned() -> None:
    """Отмена между переходом в `publishing` и POST ревью: ревью не публикуется, а шаг
    публикации не выглядит завершённым - его исхода нет."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    update = uow.review_runs.update

    def retarget_on_publishing(run: ReviewRun) -> None:
        update(run)
        if run.status is ReviewRunStatus.PUBLISHING:
            update(replace(run, status=ReviewRunStatus.CANCELLED))

    uow.review_runs.update = retarget_on_publishing  # type: ignore[method-assign]
    vcs = RecordingVcsGateway()
    _run(uow, ScriptedLlmGateway([review_response()]), vcs_gateway=vcs)

    assert _completed(uow).status is ReviewRunStatus.CANCELLED
    assert (vcs.publications, vcs.checks) == ([], [])
    (publishing,) = [
        step
        for step in uow.review_run_steps.list_for_run(JOB.review_run_id)
        if step.stage is ReviewRunStage.PUBLISHING
    ]
    assert (publishing.status, publishing.error_code) == (
        ReviewRunStepStatus.FAILED,
        "REVIEW_RUN_STALE",
    )


def test_a_run_cancelled_while_the_model_answers_is_acked() -> None:
    """Отмена чаще приходится на вызов модели: следующий переход прогона тоже бросает,
    и сообщение тоже не уходит в DLQ."""
    uow = FakeUnitOfWork()
    _seed_run(uow)

    class RetargetedGateway:
        def complete(self, request: object) -> NoReturn:
            current = uow.review_runs.get(JOB.review_run_id)
            assert current is not None
            uow.review_runs.update(replace(current, status=ReviewRunStatus.CANCELLED))
            raise LlmTimeoutError("модель не ответила")

    vcs = RecordingVcsGateway()
    _run(uow, RetargetedGateway(), vcs_gateway=vcs)

    assert _completed(uow).status is ReviewRunStatus.CANCELLED
    assert vcs.publications == []


DEFAULT_SUMMARY = "Критичных проблем в диффе не обнаружено."


def test_summary_is_the_default_one_when_the_verifier_dropped_everything() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    vcs = RecordingVcsGateway()
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "выдуманное")),
            verification_response(("F1", "drop")),
        ]
    )

    _run(uow, gateway, vcs_gateway=vcs)

    ((summary, inline),) = vcs.publications
    assert inline == ()
    assert DEFAULT_SUMMARY in summary
    assert "Есть замечания." not in summary


def test_summary_of_the_model_stays_when_some_findings_survive() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    vcs = RecordingVcsGateway()
    gateway = ScriptedLlmGateway(
        [
            review_response(
                finding_json(CHANGED_FILE, 1, "первое", category="correctness"),
                finding_json(CHANGED_FILE, 1, "второе", category="performance"),
            ),
            verification_response(("F1", "keep"), ("F2", "drop")),
        ]
    )

    _run(uow, gateway, vcs_gateway=vcs)

    ((summary, inline),) = vcs.publications
    assert len(inline) == 1
    assert "Есть замечания." in summary


def test_tokens_add_up_and_the_model_comes_from_the_review() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "x"), tokens=1000, model="qwen-review"),
            verification_response(("F1", "keep"), tokens=400, model="qwen-verify"),
        ]
    )

    _run(uow, gateway)

    run = _completed(uow)
    assert run.tokens_used == 1400
    assert run.model == "qwen-review"


RULES_MD = """\
## Error Handling Standards

- pattern: `app/**/*.py`

1. Отказ - значение Result
2. Текст ошибки воспроизводим
"""


def test_rule_reference_is_stored_and_published_with_the_attribution_prefix() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    vcs = RecordingVcsGateway(files={(".review/rules.md", JOB.base_sha): RULES_MD})
    gateway = ScriptedLlmGateway(
        [
            review_response(
                finding_json(CHANGED_FILE, 1, "Ошибка глотается.", rule=("Error Handling Standards", 2)),
                finding_json(
                    CHANGED_FILE, 1, "По правилу из PR.", rule=("No Comments Allowed", 1), category="security"
                ),
            ),
            verification_response(("F1", "keep")),
        ]
    )

    _run(uow, gateway, vcs_gateway=vcs)

    (finding,) = uow.findings.list_for_run(JOB.review_run_id)
    assert finding.rule == RuleRef("Error Handling Standards", 2, "Текст ошибки воспроизводим")
    assert _completed(uow).rejected_findings == 1
    ((_, inline),) = vcs.publications
    assert (
        "Согласно пользовательскому правилу «Error Handling Standards» "
        "(пункт 2: Текст ошибки воспроизводим): Ошибка глотается."
    ) in inline[0].body


# --- шаги прогона ---------------------------------------------------------

STAGES = [
    ReviewRunStage.FETCHING_DIFF,
    ReviewRunStage.BUILDING_CONTEXT,
    ReviewRunStage.ANALYSING,
    ReviewRunStage.VERIFYING,
    ReviewRunStage.PUBLISHING,
]


def _steps(uow: FakeUnitOfWork) -> list[tuple[ReviewRunStage, ReviewRunStepStatus, int | None, str | None]]:
    return [
        (s.stage, s.status, s.tokens_used, s.error_code)
        for s in uow.review_run_steps.list_for_run(JOB.review_run_id)
    ]


class _RetryingOnce:
    """Модель, которая перед ответом на ревью один раз повторила вызов (40 токенов)."""

    def __init__(self, scripted: ScriptedLlmGateway, error: LlmError) -> None:
        self._scripted = scripted
        self._error = error
        self._retried = False

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        if not self._retried:
            self._retried = True
            assert request.on_retry is not None
            request.on_retry(self._error, 40)
        return self._scripted.complete(request)


def test_a_successful_run_records_every_stage_completed_in_order() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "находка"), tokens=5),
            verification_response(("F1", "keep"), tokens=7),
        ]
    )

    _run(uow, gateway)

    assert _steps(uow) == [
        (ReviewRunStage.FETCHING_DIFF, ReviewRunStepStatus.COMPLETED, None, None),
        (ReviewRunStage.BUILDING_CONTEXT, ReviewRunStepStatus.COMPLETED, None, None),
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.COMPLETED, 5, None),
        (ReviewRunStage.VERIFYING, ReviewRunStepStatus.COMPLETED, 7, None),
        (ReviewRunStage.PUBLISHING, ReviewRunStepStatus.COMPLETED, None, None),
    ]
    steps = uow.review_run_steps.list_for_run(JOB.review_run_id)
    # Каждый шаг - между двумя показаниями монотонных часов, идущих на 0.5 с.
    assert [s.duration_seconds for s in steps] == [0.5] * 5
    started = [s.started_at for s in steps]
    assert started == sorted(started) and len(set(started)) == 5


def test_the_verifying_step_starts_at_the_progress_mark_before_the_verifier() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    seen: list[datetime] = []
    original = uow.review_runs.update

    def recording_update(run: ReviewRun) -> None:
        seen.append(run.last_progress_at)
        original(run)

    uow.review_runs.update = recording_update  # type: ignore[method-assign]
    gateway = ScriptedLlmGateway(
        [review_response(finding_json(CHANGED_FILE, 1, "находка")), verification_response(("F1", "keep"))]
    )

    _run(uow, gateway)

    # building_context, analysing, отметка после ревью - третья запись прогона.
    progress_mark = seen[2]
    (verifying,) = [
        s for s in uow.review_run_steps.list_for_run(JOB.review_run_id) if s.stage is ReviewRunStage.VERIFYING
    ]
    assert verifying.started_at == progress_mark


def test_a_verifier_that_was_not_needed_is_a_completed_step_without_tokens() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    _run(uow, ScriptedLlmGateway([review_response(tokens=5)]))

    assert [stage for stage, *_ in _steps(uow)] == STAGES
    assert _steps(uow)[3] == (ReviewRunStage.VERIFYING, ReviewRunStepStatus.COMPLETED, None, None)


def test_a_diff_failure_fails_the_fetching_step_with_its_code() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    with pytest.raises(VcsError):
        _run(uow, ScriptedLlmGateway([]), vcs_gateway=FakeVcs(error=VcsError("GitHub недоступен")))

    assert _steps(uow) == [
        (ReviewRunStage.FETCHING_DIFF, ReviewRunStepStatus.FAILED, None, "SCM_UNAVAILABLE"),
    ]
    (step,) = uow.review_run_steps.list_for_run(JOB.review_run_id)
    assert step.duration_seconds == 0.5


def test_a_diff_over_the_limit_fails_the_run_without_a_review() -> None:
    """Дифф больше лимита — прогон `failed` с понятной причиной, шаг
    `fetching_diff` с `DIFF_TOO_LARGE`; модель не вызывается, на PR ничего."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway([])
    vcs = RecordingVcsGateway(diff="+" * 1_001)

    with pytest.raises(DiffTooLargeError):
        _run(uow, gateway, vcs_gateway=vcs, context_limits=replace(LIMITS, diff_max_bytes=1_000))

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason == "дифф PR больше лимита 1000 байт или GitHub не строит такой большой дифф: ревью не выполняется"
    assert _steps(uow) == [
        (ReviewRunStage.FETCHING_DIFF, ReviewRunStepStatus.FAILED, None, "DIFF_TOO_LARGE"),
    ]
    assert gateway.requests == []
    assert vcs.publications == []
    assert vcs.checks == []
    assert uow.context_payloads.list_for_run(JOB.review_run_id) == []


def test_a_missing_diff_fails_the_fetching_step_as_pr_not_found() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    missing = VcsNotFoundError("GitHub API: HTTP 404 на GET /repos/owner/repo/compare/...")

    with pytest.raises(VcsNotFoundError):
        _run(uow, ScriptedLlmGateway([]), vcs_gateway=FakeVcs(error=missing))

    assert _steps(uow) == [
        (ReviewRunStage.FETCHING_DIFF, ReviewRunStepStatus.FAILED, None, "PR_NOT_FOUND"),
    ]


def test_a_rate_limited_review_fails_the_analysing_step_with_its_code() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    with pytest.raises(LlmRateLimitedError):
        _run(uow, ScriptedLlmGateway([LlmRateLimitedError("лимит запросов на всех ключах")]))

    assert _steps(uow)[-1] == (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, 0, "LLM_RATE_LIMITED")


def test_a_model_failure_fails_the_analysing_step_with_its_code_and_tokens() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    with pytest.raises(LlmTimeoutError):
        _run(uow, ScriptedLlmGateway([LlmTimeoutError("таймаут", tokens_used=30)]))

    assert _steps(uow)[2:] == [
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, 30, "LLM_TIMEOUT"),
    ]
    assert _completed(uow).failed_stage is ReviewRunStatus.ANALYSING


def test_a_program_error_fails_the_step_as_internal() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    with pytest.raises(RuntimeError):
        _run(uow, ScriptedLlmGateway([RuntimeError("ошибка программы")]))

    assert _steps(uow)[-1] == (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, None, "INTERNAL_ERROR")


def test_a_review_over_the_window_fails_the_step_without_tokens() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    with pytest.raises(PromptTooLargeError):
        _run(uow, ScriptedLlmGateway([]), llm_limits=LlmLimits(window_tokens=100, max_output_tokens=50))

    assert _steps(uow)[-1] == (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, None, "PROMPT_TOO_LARGE")


def test_a_retried_review_is_a_failed_step_and_a_new_step_of_the_same_stage() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    scripted = ScriptedLlmGateway([review_response(tokens=100)])

    _run(uow, _RetryingOnce(scripted, LlmOutputError("findings.0.severity: missing")))

    assert _steps(uow)[2:4] == [
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, 40, "LLM_INVALID_OUTPUT"),
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.COMPLETED, 60, None),
    ]
    first, second = uow.review_run_steps.list_for_run(JOB.review_run_id)[2:4]
    assert second.started_at > first.started_at
    assert _completed(uow).tokens_used == 100


def test_a_retried_review_that_fails_again_records_both_attempts() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    scripted = ScriptedLlmGateway([LlmOutputError("всё ещё не по схеме", tokens_used=90)])

    with pytest.raises(LlmOutputError):
        _run(uow, _RetryingOnce(scripted, LlmOutputError("не по схеме")))

    assert _steps(uow)[2:] == [
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, 40, "LLM_INVALID_OUTPUT"),
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, 50, "LLM_INVALID_OUTPUT"),
    ]


def test_a_failed_verifier_is_a_failed_step_but_the_run_goes_on() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "находка"), tokens=5),
            LlmError("модель недоступна", tokens_used=3),
        ]
    )

    _run(uow, gateway)

    assert _steps(uow)[3:] == [
        (ReviewRunStage.VERIFYING, ReviewRunStepStatus.FAILED, 3, "LLM_UNAVAILABLE"),
        (ReviewRunStage.PUBLISHING, ReviewRunStepStatus.COMPLETED, None, None),
    ]
    assert _completed(uow).status is ReviewRunStatus.COMPLETED


def test_verdicts_not_covering_the_candidates_fail_the_verifying_step() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "находка")),
            verification_response(("F9", "keep"), tokens=4),
        ]
    )

    _run(uow, gateway)

    assert _steps(uow)[3] == (ReviewRunStage.VERIFYING, ReviewRunStepStatus.FAILED, 4, "LLM_INVALID_OUTPUT")
    assert _completed(uow).verification is VerificationOutcome.UNAVAILABLE


def test_a_failure_while_closing_the_step_does_not_mask_the_original_error() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    real_update = uow.review_run_steps.update

    def update(step) -> None:
        if step.status is ReviewRunStepStatus.FAILED:
            raise ConnectionError("база недоступна")
        real_update(step)

    uow.review_run_steps.update = update  # type: ignore[method-assign]

    with pytest.raises(LlmError, match="модель недоступна"):
        _run(uow, ScriptedLlmGateway([LlmError("модель недоступна")]))

    assert _completed(uow).status is ReviewRunStatus.FAILED
    assert _steps(uow)[-1][:2] == (ReviewRunStage.ANALYSING, ReviewRunStepStatus.RUNNING)


def _failing_step_writes(
    uow: FakeUnitOfWork, fails: Callable[[ReviewRunStep], bool]
) -> list[ReviewRunStep]:
    """Первая запись шага, для которой `fails` истинно, падает как сбой базы; такие шаги — в списке.

    Сбой разовый, как у кратковременного сбоя базы: повторная запись того же
    шага в том же виде проходит.
    """
    failed: list[ReviewRunStep] = []
    real_add, real_update = uow.review_run_steps.add, uow.review_run_steps.update

    def guarded(write: Callable[[ReviewRunStep], None]) -> Callable[[ReviewRunStep], None]:
        def wrapper(step: ReviewRunStep) -> None:
            if fails(step) and step not in failed:
                failed.append(step)
                raise ConnectionError("база недоступна")
            write(step)

        return wrapper

    uow.review_run_steps.add = guarded(real_add)  # type: ignore[method-assign,assignment]
    uow.review_run_steps.update = guarded(real_update)  # type: ignore[method-assign,assignment]
    return failed


def _fallback(
    local: list[LlmResponse[Any] | Exception], hosted: list[LlmResponse[Any] | Exception]
) -> FallbackLlmGateway:
    return FallbackLlmGateway(
        [("local", ScriptedLlmGateway(local)), ("hosted", ScriptedLlmGateway(hosted))]
    )


def test_a_failed_retry_write_hands_the_review_to_the_next_provider() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    failed = _failing_step_writes(
        uow, lambda s: s.stage is ReviewRunStage.ANALYSING and s.status is ReviewRunStepStatus.FAILED
    )
    gateway = _fallback(
        [LlmError("локальная модель недоступна", tokens_used=10)],
        [review_response(tokens=5)],
    )

    _run(uow, gateway)

    assert [s.error_code for s in failed] == ["LLM_UNAVAILABLE"]
    run = _completed(uow)
    assert run.status is ReviewRunStatus.COMPLETED
    assert run.tokens_used == 15
    # Исход неудавшейся попытки не записался сразу - его пишет завершение прогона;
    # повтор записан своим шагом.
    assert _steps(uow)[2:] == [
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, 10, "LLM_UNAVAILABLE"),
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.COMPLETED, 5, None),
        (ReviewRunStage.VERIFYING, ReviewRunStepStatus.COMPLETED, None, None),
        (ReviewRunStage.PUBLISHING, ReviewRunStepStatus.COMPLETED, None, None),
    ]


def test_a_failed_retry_start_write_keeps_the_review_going() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    analysing_adds = 0

    def second_analysing_start(step: ReviewRunStep) -> bool:
        nonlocal analysing_adds
        if step.stage is not ReviewRunStage.ANALYSING or step.status is not ReviewRunStepStatus.RUNNING:
            return False
        analysing_adds += 1
        return analysing_adds == 2

    _failing_step_writes(uow, second_analysing_start)
    gateway = _fallback([LlmError("локальная модель недоступна", tokens_used=10)], [review_response(tokens=5)])

    _run(uow, gateway)

    assert _completed(uow).status is ReviewRunStatus.COMPLETED
    # Начало повтора не записалось — закрывать нечего; следующие этапы пишутся как обычно.
    assert _steps(uow)[2:] == [
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, 10, "LLM_UNAVAILABLE"),
        (ReviewRunStage.VERIFYING, ReviewRunStepStatus.COMPLETED, None, None),
        (ReviewRunStage.PUBLISHING, ReviewRunStepStatus.COMPLETED, None, None),
    ]


def test_a_failed_retry_write_hands_the_verifier_to_the_next_provider() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    failed = _failing_step_writes(
        uow, lambda s: s.stage is ReviewRunStage.VERIFYING and s.status is ReviewRunStepStatus.FAILED
    )
    gateway = _fallback(
        [
            review_response(finding_json(CHANGED_FILE, 1, "находка"), tokens=5),
            LlmError("локальная модель недоступна", tokens_used=2),
        ],
        [verification_response(("F1", "keep"), tokens=7)],
    )

    _run(uow, gateway)

    assert [s.error_code for s in failed] == ["LLM_UNAVAILABLE"]
    run = _completed(uow)
    assert run.status is ReviewRunStatus.COMPLETED
    # Верификатор ответил через резервного провайдера: fail-open не понадобился.
    assert run.verification is VerificationOutcome.VERIFIED
    assert run.tokens_used == 5 + 2 + 7
    assert _steps(uow)[3:] == [
        (ReviewRunStage.VERIFYING, ReviewRunStepStatus.FAILED, 2, "LLM_UNAVAILABLE"),
        (ReviewRunStage.VERIFYING, ReviewRunStepStatus.COMPLETED, 7, None),
        (ReviewRunStage.PUBLISHING, ReviewRunStepStatus.COMPLETED, None, None),
    ]


def test_a_failed_step_commit_after_publishing_does_not_fail_the_run() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    vcs = RecordingVcsGateway()
    failed = _failing_step_writes(
        uow, lambda s: s.stage is ReviewRunStage.PUBLISHING and s.status is ReviewRunStepStatus.COMPLETED
    )
    gateway = ScriptedLlmGateway(
        [review_response(finding_json(CHANGED_FILE, 1, "находка")), verification_response(("F1", "keep"))]
    )

    _run(uow, gateway, vcs_gateway=vcs)

    assert len(failed) == 1
    assert vcs.publications
    run = _completed(uow)
    assert run.status is ReviewRunStatus.COMPLETED
    assert run.failure_reason is None
    # Исход шага не записался сразу: его пишет завершение прогона, и шагов
    # `running` у завершённого прогона нет.
    assert _steps(uow)[-1] == (ReviewRunStage.PUBLISHING, ReviewRunStepStatus.COMPLETED, None, None)
    assert all(s[1] is not ReviewRunStepStatus.RUNNING for s in _steps(uow))


def test_failed_step_starts_do_not_fail_the_run() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    _failing_step_writes(uow, lambda s: s.status is ReviewRunStepStatus.RUNNING)

    _run(uow, ScriptedLlmGateway([review_response(tokens=5)]))

    assert _completed(uow).status is ReviewRunStatus.COMPLETED
    assert _steps(uow) == []


def test_a_screening_failure_keeps_the_review_tokens_on_the_analysing_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    def broken_screening(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("отсев сломан")

    monkeypatch.setattr("app.application.review_pipeline.screen_findings", broken_screening)

    with pytest.raises(RuntimeError, match="отсев сломан"):
        _run(uow, ScriptedLlmGateway([review_response(tokens=25)]))

    # Отсев - часть этапа: шаг один, упавший, с токенами ответа модели.
    assert _steps(uow)[2:] == [
        (ReviewRunStage.ANALYSING, ReviewRunStepStatus.FAILED, 25, "INTERNAL_ERROR"),
    ]
    assert _completed(uow).status is ReviewRunStatus.FAILED


def test_a_redelivered_run_closes_the_step_the_dead_worker_left_running() -> None:
    """Воркер умер посреди `analysing`, брокер доставил сообщение снова."""
    uow = FakeUnitOfWork()
    _seed_run(uow, status=ReviewRunStatus.ANALYSING)
    uow.review_run_steps.add(
        start_step(JOB.review_run_id, ReviewRunStage.ANALYSING, started_at=NOW, step_id=UUID(int=50))
    )

    with pytest.raises(ValueError):
        _run(uow, ScriptedLlmGateway([review_response()]))

    assert _completed(uow).status is ReviewRunStatus.FAILED
    (abandoned,) = uow.review_run_steps.list_for_run(JOB.review_run_id)
    assert abandoned.status is ReviewRunStepStatus.FAILED
    assert abandoned.error_code == "REVIEW_RUN_STALE"
    # Длительность - по стенным часам до момента сбоя: часы теста идут на секунду за чтение.
    assert abandoned.duration_seconds is not None and abandoned.duration_seconds > 0


def test_a_failed_run_closes_its_step_with_the_known_code_when_the_outcome_write_failed() -> None:
    """Исход упавшего шага не записался - перевод прогона в failed пишет его с настоящим кодом."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    writes = {"failed": 0}
    real_update = uow.review_run_steps.update

    def update(step: ReviewRunStep) -> None:
        if step.status is ReviewRunStepStatus.FAILED and not writes["failed"]:
            writes["failed"] += 1
            raise ConnectionError("база недоступна")
        real_update(step)

    uow.review_run_steps.update = update  # type: ignore[method-assign]

    with pytest.raises(LlmError):
        _run(uow, ScriptedLlmGateway([LlmError("модель недоступна")]))

    assert _completed(uow).status is ReviewRunStatus.FAILED
    assert _steps(uow)[-1] == (
        ReviewRunStage.ANALYSING,
        ReviewRunStepStatus.FAILED,
        0,
        "LLM_UNAVAILABLE",
    )


def test_a_failure_saving_the_findings_fails_the_publishing_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    def broken_add(finding: object) -> NoReturn:
        raise RuntimeError("нарушено ограничение")

    monkeypatch.setattr(uow.findings, "add", broken_add)
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(CHANGED_FILE, 1, "находка"), tokens=5),
            verification_response(("F1", "keep"), tokens=7),
        ]
    )

    with pytest.raises(RuntimeError, match="нарушено ограничение"):
        _run(uow, gateway)

    assert _steps(uow)[3:] == [
        (ReviewRunStage.VERIFYING, ReviewRunStepStatus.COMPLETED, 7, None),
        (ReviewRunStage.PUBLISHING, ReviewRunStepStatus.FAILED, None, "INTERNAL_ERROR"),
    ]
    run = _completed(uow)
    assert run.status is ReviewRunStatus.FAILED
    # Токены ответов, не дошедшие до прогона вместе с замечаниями, не теряются.
    assert run.tokens_used == 12


def test_a_failed_transition_to_analysing_fails_the_analysing_step() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    real_update = uow.review_runs.update

    def update(run: ReviewRun) -> None:
        if run.status is ReviewRunStatus.ANALYSING:
            raise ConnectionError("база недоступна")
        real_update(run)

    uow.review_runs.update = update  # type: ignore[method-assign]

    with pytest.raises(ConnectionError):
        _run(uow, ScriptedLlmGateway([review_response()]))

    assert _steps(uow)[-1] == (
        ReviewRunStage.ANALYSING,
        ReviewRunStepStatus.FAILED,
        None,
        "INTERNAL_ERROR",
    )
    assert _completed(uow).status is ReviewRunStatus.FAILED


def test_a_screening_failure_counts_the_review_tokens_in_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    def broken_screening(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("отсев сломан")

    monkeypatch.setattr("app.application.review_pipeline.screen_findings", broken_screening)

    with pytest.raises(RuntimeError):
        _run(uow, ScriptedLlmGateway([review_response(tokens=25)]))

    assert _completed(uow).tokens_used == 25


def _seed_earlier_review(uow: FakeUnitOfWork, *, run_id: UUID, at: datetime) -> None:
    """Прогон на тот же head рядом с текущим: `completed` раньше или новый позже."""
    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    uow.review_runs.add(
        replace(run, id=run_id, status=ReviewRunStatus.COMPLETED, created_at=at, updated_at=at)
    )


def test_a_failed_review_after_an_earlier_one_blocks_the_head() -> None:
    """Ревью #225: после смены ветки поправка gate не дошла, на head `success` прежнего
    прогона по старому диффу - упавший новый прогон выставляет `failure`."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    _seed_earlier_review(uow, run_id=UUID(int=55), at=NOW - timedelta(minutes=5))
    vcs = RecordingVcsGateway(diff="+" * 1_001)

    with pytest.raises(DiffTooLargeError):
        _run(uow, ScriptedLlmGateway([]), vcs_gateway=vcs, context_limits=replace(LIMITS, diff_max_bytes=1_000))

    assert vcs.checks == [build_failed_review_check()]


def test_a_failed_review_superseded_on_the_head_leaves_the_gate_alone() -> None:
    """За head отвечает уже более поздний прогон - упавший старый gate не трогает."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    _seed_earlier_review(uow, run_id=UUID(int=55), at=NOW + timedelta(minutes=5))
    vcs = RecordingVcsGateway(diff="+" * 1_001)

    with pytest.raises(DiffTooLargeError):
        _run(uow, ScriptedLlmGateway([]), vcs_gateway=vcs, context_limits=replace(LIMITS, diff_max_bytes=1_000))

    assert vcs.checks == []


def test_a_failed_gate_of_a_failed_review_does_not_hide_the_failure() -> None:
    """GitHub не принял `failure`: в логе сбой, наружу - исходное исключение прогона."""
    uow = FakeUnitOfWork()
    _seed_run(uow)
    _seed_earlier_review(uow, run_id=UUID(int=55), at=NOW - timedelta(minutes=5))
    vcs = RecordingVcsGateway(diff="+" * 1_001)
    vcs.check_error = VcsTransientError("GitHub API: HTTP 502")

    with pytest.raises(DiffTooLargeError):
        _run(uow, ScriptedLlmGateway([]), vcs_gateway=vcs, context_limits=replace(LIMITS, diff_max_bytes=1_000))

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
