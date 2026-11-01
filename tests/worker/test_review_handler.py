"""Обработчик воркера: разбор сообщения очереди + `run_review`. Без БД и брокера."""

import io
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.application.context_assembly import ContextLimits
from app.application.llm_review import LlmLimits
from app.application.ports.llm_gateway import LlmGateway
from app.domain.entities import MergeRequest, ReviewJob, ReviewRun
from app.domain.enums import (
    MergeRequestState,
    ReviewRunStatus,
    TriggerSource,
    VerificationOutcome,
)
from app.domain.llm_errors import LlmTimeoutError
from app.domain.prompt import PromptTexts
from app.infrastructure.llm.stub import StubLlmGateway
from app.infrastructure.logging import (
    JsonFormatter,
    ReviewRunIdFilter,
    TraceContextFilter,
)
from app.infrastructure.queue.rabbitmq import to_wire_message
from app.infrastructure.telemetry import OtelRunOutcomeRecorder
from app.worker.factory import build_review_handler

from ..fakes.context import FakeCache, FakeExtractor
from ..fakes.llm_gateway import ScriptedLlmGateway, review_response
from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.vcs_gateway import RecordingVcsGateway
from ..fakes.webhooks import a_repository
from ..metrics import metric_delta, metric_snapshot

LIMITS = ContextLimits(32_000, 4_000, 32_768, 60, 20, 262_144, 1_000, diff_max_bytes=5_242_880)
LLM_LIMITS = LlmLimits(window_tokens=65_536, max_output_tokens=4_096)
TEXTS = PromptTexts(review_system="ревью", verifier_system="верификация")

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


class _Deps:
    """Заглушка вместо `Container`: только то, что нужно обработчику."""

    def __init__(self, uow: FakeUnitOfWork, gateway: LlmGateway | None = None) -> None:
        self._uow = uow
        self._gateway = gateway or StubLlmGateway()

    def unit_of_work(self) -> FakeUnitOfWork:
        return self._uow

    def llm_gateway(self) -> LlmGateway:
        return self._gateway

    def vcs_gateway(self) -> RecordingVcsGateway:
        return RecordingVcsGateway()

    def definition_extractor(self) -> FakeExtractor:
        return FakeExtractor()

    def cache_store(self) -> FakeCache:
        return FakeCache()


def _seeded_uow(status: ReviewRunStatus = ReviewRunStatus.QUEUED) -> FakeUnitOfWork:
    """База с открытым PR и прогоном задачи в `status`."""
    now = datetime(2026, 9, 29, tzinfo=UTC)
    uow = FakeUnitOfWork()
    mr_id = UUID(int=3)
    # Запись репозитория PR: без неё merge gate не прочитает порог.
    uow.repositories.add(replace(a_repository(), id=UUID(int=9)))
    uow.merge_requests.add(
        MergeRequest(
            id=mr_id,
            repository_id=UUID(int=9),
            number=JOB.pull_request_number,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="develop",
            head_sha=JOB.head_sha,
            state=MergeRequestState.OPEN,
            created_at=now,
            updated_at=now,
        )
    )
    uow.review_runs.add(
        ReviewRun(
            id=JOB.review_run_id,
            merge_request_id=mr_id,
            head_sha=JOB.head_sha,
            status=status,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    return uow


def test_handler_parses_the_message_and_drives_the_run_to_completed() -> None:
    uow = _seeded_uow()
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")

    handler = build_review_handler(_Deps(uow), LIMITS, LLM_LIMITS, TEXTS)
    handler(body)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.model == "stub-llm"
    assert run.verification is VerificationOutcome.NOT_REQUIRED


def test_handler_passes_the_output_limit_to_the_model() -> None:
    gateway = ScriptedLlmGateway([review_response()])
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")

    build_review_handler(_Deps(_seeded_uow(), gateway), LIMITS, LLM_LIMITS, TEXTS)(body)

    (request,) = gateway.requests
    assert request.max_output_tokens == 4_096


def test_handler_logs_both_ids_when_the_run_fails(caplog: pytest.LogCaptureFixture) -> None:
    """Лог упавшей задачи: `job.id` — в тексте, `review_run_id` — атрибут записи.

    Прогона нет — обработка падает; идентификатор прогона приносят и контекст
    логов, и спан обработки, поэтому в тексте ему делать нечего (спека «Логи»).
    Хендлер здесь — производственная связка `configure_logging` (JSON + оба
    фильтра) со своим потоком: порядок фильтров детерминирован тестом, а не
    порядком хендлеров root из прошлых тестов.
    """
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")
    handler = build_review_handler(_Deps(FakeUnitOfWork()), LIMITS, LLM_LIMITS, TEXTS)
    out = io.StringIO()
    record_handler = logging.StreamHandler(out)
    record_handler.setFormatter(JsonFormatter("dmc-268-test"))
    record_handler.addFilter(TraceContextFilter())
    record_handler.addFilter(ReviewRunIdFilter())
    worker_logger = logging.getLogger("app.worker.factory")
    worker_logger.addHandler(record_handler)

    with caplog.at_level(logging.ERROR), pytest.raises(LookupError):
        handler(body)

    worker_logger.removeHandler(record_handler)
    # Атрибут и текст — на одной записи производственного JSON-хендлера.
    [fields] = [json.loads(line) for line in out.getvalue().splitlines() if line]
    assert fields["review_run_id"] == str(JOB.review_run_id)
    assert str(JOB.id) in fields["message"]
    assert str(JOB.review_run_id) not in fields["message"]
    # Та же запись уходит дальше по root без потерь.
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert str(JOB.review_run_id) not in record.getMessage()


def test_a_message_without_a_run_is_not_counted_as_failed(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Сообщение без прогона (`LookupError`) — не исход прогона: счётчик не растёт.

    Считает не обработчик, а пайплайн на фактическом терминальном переходе:
    прогона нет — считать нечего.
    """
    before = metric_snapshot(metric_reader)
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")

    with pytest.raises(LookupError):
        build_review_handler(
            _Deps(FakeUnitOfWork()),
            LIMITS,
            LLM_LIMITS,
            TEXTS,
            run_outcome=OtelRunOutcomeRecorder(),
        )(body)

    failed, _ = metric_delta(metric_reader, before, "review_runs_total", status="failed")
    assert failed == 0
    completed, _ = metric_delta(
        metric_reader, before, "review_runs_total", status="completed"
    )
    assert completed == 0


def test_a_redelivered_terminal_run_is_not_counted(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Повторная доставка терминального прогона возвращается молча — счётчик не растёт."""
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")
    before = metric_snapshot(metric_reader)

    build_review_handler(
        _Deps(_seeded_uow(ReviewRunStatus.COMPLETED)),
        LIMITS,
        LLM_LIMITS,
        TEXTS,
        run_outcome=OtelRunOutcomeRecorder(),
    )(body)

    failed, _ = metric_delta(metric_reader, before, "review_runs_total", status="failed")
    assert failed == 0
    completed, _ = metric_delta(
        metric_reader, before, "review_runs_total", status="completed"
    )
    assert completed == 0


def test_a_failed_run_grows_the_run_counter_by_status(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Упавший прогон растит `review_runs_total{status="failed"}` ровно один раз.

    Счётчик пишет пайплайн на переходе в failed (`_mark_failed`), а не
    обработчик на каждый сбой обработки.
    """
    before = metric_snapshot(metric_reader)
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")

    with pytest.raises(LlmTimeoutError):
        build_review_handler(
            _Deps(
                _seeded_uow(),
                ScriptedLlmGateway([LlmTimeoutError("таймаут", tokens_used=1)]),
            ),
            LIMITS,
            LLM_LIMITS,
            TEXTS,
            run_outcome=OtelRunOutcomeRecorder(),
        )(body)

    failed, _ = metric_delta(metric_reader, before, "review_runs_total", status="failed")
    assert failed == 1
    completed, _ = metric_delta(
        metric_reader, before, "review_runs_total", status="completed"
    )
    assert completed == 0


def test_a_completed_run_grows_the_run_counter_by_status(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Дошедший до терминального статуса прогон растит `review_runs_total{status="completed"}`."""
    before = metric_snapshot(metric_reader)
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")

    build_review_handler(
        _Deps(_seeded_uow()),
        LIMITS,
        LLM_LIMITS,
        TEXTS,
        run_outcome=OtelRunOutcomeRecorder(),
    )(body)

    completed, _ = metric_delta(
        metric_reader, before, "review_runs_total", status="completed"
    )
    assert completed == 1
    failed, _ = metric_delta(metric_reader, before, "review_runs_total", status="failed")
    assert failed == 0
