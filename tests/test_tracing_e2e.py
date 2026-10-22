"""Сквозной трейс прогона: вебхук → постановка в RabbitMQ → воркер по этапам.

Настоящие эндпоинт, use case, база, брокер и consume-цикл воркера; GitHub и модель —
двойники. Нужны `TEST_DATABASE_URL` и `TEST_RABBITMQ_URL`, как остальным
интеграционным. Спаны пишет в память провайдер из `tests/conftest.py`.
"""

from collections.abc import Iterator
from uuid import UUID

import pika
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, format_trace_id

from app.api.factory import create_app
from app.config import Settings
from app.domain.enums import ReviewRunStatus
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.llm.stub import StubLlmGateway
from app.infrastructure.queue.rabbitmq import (
    DLQ_NAME,
    QUEUE_NAME,
    RabbitMQJobQueue,
    declare_topology,
)
from app.infrastructure.telemetry import instrument_engine
from app.worker.factory import build_review_handler, build_worker

from .api.test_webhooks_e2e import SAMPLE_DIFF, SECRET, post_webhook
from .conftest import TEST_RABBITMQ_URL, requires_broker, requires_db
from .fakes import FakeContainer, FakeVcs, a_repository
from .fakes.context import FakeCache, FakeExtractor
from .fakes.vcs_gateway import RecordingVcsGateway
from .worker.test_review_handler import LIMITS, LLM_LIMITS, TEXTS

pytestmark = [pytest.mark.integration, requires_db, requires_broker]

STAGE_SPANS = {
    "review.fetching_diff",
    "review.building_context",
    "review.analysing",
    "review.verifying",
    "review.publishing",
}


@pytest.fixture
def broker() -> Iterator[None]:
    assert TEST_RABBITMQ_URL is not None
    connection = pika.BlockingConnection(pika.URLParameters(TEST_RABBITMQ_URL))
    channel = connection.channel()
    declare_topology(channel)
    channel.queue_purge(QUEUE_NAME)
    channel.queue_purge(DLQ_NAME)
    try:
        yield
    finally:
        channel.queue_purge(QUEUE_NAME)
        channel.queue_purge(DLQ_NAME)
        connection.close()


class _WorkerDeps:
    def __init__(self, engine) -> None:
        self._engine = engine

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._engine)

    def llm_gateway(self) -> StubLlmGateway:
        return StubLlmGateway()

    def vcs_gateway(self) -> RecordingVcsGateway:
        return RecordingVcsGateway()

    def definition_extractor(self) -> FakeExtractor:
        return FakeExtractor()

    def cache_store(self) -> FakeCache:
        return FakeCache()


def _trace_id(span: ReadableSpan) -> str:
    assert span.context is not None
    return format_trace_id(span.context.trace_id)


def _span_id(span: ReadableSpan) -> int:
    context = span.get_span_context()
    assert context is not None
    return context.span_id


def test_one_trace_links_the_webhook_the_queue_and_every_worker_stage(
    clean_db, broker: None, spans: InMemorySpanExporter
) -> None:
    assert TEST_RABBITMQ_URL is not None
    repository = a_repository()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.repositories.add(repository)
        work.commit()
    instrument_engine(clean_db)
    settings = Settings(
        database_url=str(clean_db.url),
        rabbitmq_url=TEST_RABBITMQ_URL,
        github_webhook_secret=SECRET,
    )
    app = create_app(settings)
    app.state.container = FakeContainer(
        engine=clean_db,
        settings=settings,
        vcs=FakeVcs(diff=SAMPLE_DIFF),
        queue=RabbitMQJobQueue(TEST_RABBITMQ_URL),  # type: ignore[arg-type]
    )

    response = post_webhook(TestClient(app), "opened")
    assert response.status_code == 202
    run_id = response.json()["id"]

    handler = build_review_handler(_WorkerDeps(clean_db), LIMITS, LLM_LIMITS, TEXTS)
    build_worker(settings).run(handler, limit=1)

    with SqlAlchemyUnitOfWork(clean_db) as work:
        run = work.review_runs.get(UUID(run_id))
    assert run is not None and run.status is ReviewRunStatus.COMPLETED

    finished = spans.get_finished_spans()
    (server,) = [s for s in finished if s.kind is SpanKind.SERVER]
    (publish,) = [s for s in finished if s.kind is SpanKind.PRODUCER]
    (process,) = [s for s in finished if s.kind is SpanKind.CONSUMER]
    stages = [s for s in finished if s.name in STAGE_SPANS]

    # Один трейс от запроса вебхука до публикации ревью, и это тот trace id,
    # который клиент получил в `X-Trace-ID`.
    trace_id = _trace_id(server)
    assert response.headers["X-Trace-ID"] == trace_id
    assert {_trace_id(s) for s in [publish, process, *stages]} == {trace_id}
    assert process.parent is not None
    assert process.parent.span_id == _span_id(publish)
    assert process.attributes is not None
    assert process.attributes["review_run_id"] == run_id

    # Каждый этап — спан с `review_run_id`, ребёнок спана обработки сообщения.
    assert {s.name for s in stages} == STAGE_SPANS
    for stage in stages:
        assert stage.attributes is not None
        assert stage.attributes["review_run_id"] == run_id
        assert stage.parent is not None
        assert stage.parent.span_id == _span_id(process)

    # Запросы к базе этапа — его дети: SQLAlchemy инструментирован и на 2.1.
    stage_span_ids = {_span_id(s) for s in stages}
    database = [
        s
        for s in finished
        if s.kind is SpanKind.CLIENT
        and s.parent is not None
        and s.parent.span_id in stage_span_ids
    ]
    assert database
    assert {_trace_id(s) for s in database} == {trace_id}
