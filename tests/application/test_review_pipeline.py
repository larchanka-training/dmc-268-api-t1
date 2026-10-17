"""Оркестрация одного прогона ревью — фейковый `UnitOfWork`, без БД и брокера."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID

import pytest

from app.application.context_assembly import ContextLimits
from app.application.llm_review import LlmLimits
from app.application.ports.llm_gateway import LlmTask
from app.application.review_pipeline import run_review
from app.domain.entities import MergeRequest, ReviewJob, ReviewRun, RuleRef
from app.domain.enums import (
    MergeRequestState,
    ReviewRunStatus,
    TriggerSource,
    VerificationOutcome,
)
from app.domain.llm_errors import LlmError, LlmOutputError, PromptTooLargeError
from app.domain.prompt import PromptTexts
from app.domain.vcs_errors import VcsError

from ..fakes.context import FakeCache, FakeExtractor
from ..fakes.llm_gateway import (
    ScriptedLlmGateway,
    finding_json,
    review_response,
    verification_response,
)
from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.vcs_gateway import RecordingVcsGateway
from ..fakes.webhooks import FakeVcs

# Дифф фейкового VCS меняет первую строку `app/main.py`.
CHANGED_FILE = "app/main.py"
LIMITS = ContextLimits(32_000, 4_000, 32_768, 60, 20, 262_144, 1_000)
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
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


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
        "monotonic": _Monotonic(0.0, 12.5),
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

    _run(uow, gateway)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.model == "stub-llm"
    assert run.tokens_used == 0
    assert run.duration_seconds == 12.5
    assert uow.context_payloads.list_for_run(JOB.review_run_id)


def test_context_is_built_from_the_real_diff() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    _run(uow, ScriptedLlmGateway([review_response()]))

    (payload,) = uow.context_payloads.list_for_run(JOB.review_run_id)
    assert payload.file_paths == (CHANGED_FILE,)
    assert payload.body["schema"] == "context-payload/v1"
    assert "+new" in payload.body["diff"][0]["hunk"]


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

    # building_context, analysing, итог анализа, publishing, completed
    assert len(seen) == 5
    assert seen == sorted(seen)
    assert len(set(seen)) == 5


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
