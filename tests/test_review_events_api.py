"""Контракт `GET /api/v1/reviews/{id}/events`. Без БД и без настоящего ожидания."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.dependencies import get_container, get_run_reader, get_sleep
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus as S
from app.domain.enums import TriggerSource
from app.domain.ids import new_id

from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
RUN_ID = UUID("00000000-0000-0000-0000-0000000000aa")

RUN = ReviewRun(
    id=RUN_ID,
    merge_request_id=UUID(int=2),
    head_sha="abc123",
    status=S.QUEUED,
    trigger=TriggerSource.WEBHOOK,
    last_progress_at=NOW,
    created_at=NOW,
    updated_at=NOW,
)

SNAPSHOT = (
    'data: {"review_run_id":"00000000-0000-0000-0000-0000000000aa",'
    '"status":"%s","last_progress_at":"2026-10-13T12:00:00Z",'
    '"failed_stage":%s,"failure_reason":%s}\n'
)


def _client(*states: ReviewRun | None) -> TestClient:
    """Приложение, где каждое чтение прогона отдаёт следующее состояние из списка."""
    app = create_app(SETTINGS)
    remaining = list(states)

    def read(run_id: UUID) -> ReviewRun | None:
        assert run_id == RUN_ID
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    async def no_sleep(seconds: float) -> None:
        return None

    app.dependency_overrides[get_run_reader] = lambda: read
    app.dependency_overrides[get_sleep] = lambda: no_sleep
    return TestClient(app)


def test_unknown_run_is_404_and_no_stream_opens() -> None:
    response = _client(None).get(f"/api/v1/reviews/{RUN_ID}/events")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"


def test_the_stream_forbids_caching_and_proxy_buffering() -> None:
    response = _client(replace(RUN, status=S.COMPLETED)).get(f"/api/v1/reviews/{RUN_ID}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


def test_a_finished_run_gets_the_retry_hint_one_snapshot_and_the_stream_ends() -> None:
    failed = replace(
        RUN, status=S.FAILED, failed_stage=S.ANALYSING, failure_reason="модель недоступна"
    )

    response = _client(failed).get(f"/api/v1/reviews/{RUN_ID}/events")

    assert response.text == (
        "retry: 3000\n\n"
        "event: status\n"
        + SNAPSHOT % ("failed", '"analysing"', '"модель недоступна"')
        + "\n"
    )


def test_a_running_run_streams_its_changes_until_it_finishes() -> None:
    # Первое чтение — проверка существования до начала потока.
    client = _client(
        RUN,
        RUN,
        RUN,
        replace(RUN, status=S.ANALYSING),
        replace(RUN, status=S.COMPLETED),
    )

    response = client.get(f"/api/v1/reviews/{RUN_ID}/events")

    assert response.text == (
        "retry: 3000\n\n"
        "event: status\n" + SNAPSHOT % ("queued", "null", "null") + "\n"
        "event: status\n" + SNAPSHOT % ("analysing", "null", "null") + "\n"
        "event: status\n" + SNAPSHOT % ("completed", "null", "null") + "\n"
    )


def test_a_malformed_id_is_rejected_before_any_read() -> None:
    response = _client(RUN).get("/api/v1/reviews/not-a-uuid/events")

    assert response.status_code == 422


class _CountingContainer:
    """Контейнер, который считает выданные единицы работы."""

    def __init__(self, uow: FakeUnitOfWork) -> None:
        self._uow = uow
        self.issued = 0

    def unit_of_work(self) -> FakeUnitOfWork:
        self.issued += 1
        return self._uow


def test_each_read_takes_its_own_short_unit_of_work() -> None:
    uow = FakeUnitOfWork()
    run = replace(RUN, id=new_id())
    uow.review_runs.add(run)
    container = _CountingContainer(uow)

    read = get_run_reader(container)  # type: ignore[arg-type]

    assert read(run.id) == run
    assert read(run.id) == run
    assert read(new_id()) is None
    assert container.issued == 3


def test_the_real_reader_is_wired_to_the_container() -> None:
    app = create_app(SETTINGS)
    uow = FakeUnitOfWork()
    uow.review_runs.add(replace(RUN, status=S.CANCELLED))
    app.dependency_overrides[get_container] = lambda: _CountingContainer(uow)

    response = TestClient(app).get(f"/api/v1/reviews/{RUN_ID}/events")

    assert response.status_code == 200
    assert '"status":"cancelled"' in response.text
