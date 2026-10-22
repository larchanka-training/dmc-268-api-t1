"""Контракт `GET /api/v1/events`. Без БД, сети и настоящего ожидания.

Поток бесконечен, поэтому каждый сценарий кончается так же, как в жизни: сессия
истекает, и поток закрывается на ближайшей проверке. Часы теста идут ожиданием.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from starlette.types import Message

from app.api.dependencies import (
    get_clock,
    get_installation_catalog,
    get_sleep,
    get_untraced_unit_of_work_factory,
)
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import (
    DiffAnchor,
    Finding,
    InstallationRepository,
    MergeRequest,
    Repository,
    ReviewRun,
)
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    TriggerSource,
)
from app.domain.enums import ReviewRunStatus as S
from app.domain.ids import new_id
from app.domain.vcs_errors import VcsError

from .fakes.auth import sign_in_with_session
from .fakes.installation_catalog import FakeInstallationCatalog
from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)
PATH = "/api/v1/events"
# Время подключения — настоящие часы сервера; прогоны «после подключения» — с запасом.
FUTURE = datetime.now(UTC) + timedelta(hours=1)
PAST = datetime.now(UTC) - timedelta(hours=1)

VISIBLE = InstallationRepository("101", "octo-org/api", "main", installation_id=7, private=True)
HIDDEN = InstallationRepository("202", "octo-org/secret", "main", installation_id=7, private=True)


def _repository(provider_id: str, full_name: str) -> Repository:
    return Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id=provider_id,
        full_name=full_name,
        default_branch="main",
        auto_review_enabled=True,
        created_at=PAST,
        updated_at=PAST,
    )


def _merge_request(repository: Repository) -> MergeRequest:
    return MergeRequest(
        id=new_id(),
        repository_id=repository.id,
        number=42,
        title="Фича",
        description="",
        author="octocat",
        source_branch="feat",
        target_branch="main",
        head_sha="abc123",
        state=MergeRequestState.OPEN,
        created_at=PAST,
        updated_at=PAST,
    )


def _run(merge_request: MergeRequest, status: S, updated_at: datetime, created_at: datetime = PAST) -> ReviewRun:
    return ReviewRun(
        id=new_id(),
        merge_request_id=merge_request.id,
        head_sha="abc123",
        status=status,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=updated_at,
        created_at=created_at,
        updated_at=updated_at,
    )


class World:
    """Фейковая база с двумя репозиториями: один виден пользователю у провайдера, другой — нет."""

    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.visible = _repository(VISIBLE.provider_id, VISIBLE.full_name)
        self.hidden = _repository(HIDDEN.provider_id, HIDDEN.full_name)
        self.visible_mr = _merge_request(self.visible)
        self.hidden_mr = _merge_request(self.hidden)
        for repository in (self.visible, self.hidden):
            self.uow.repositories.add(repository)
        for merge_request in (self.visible_mr, self.hidden_mr):
            self.uow.merge_requests.add(merge_request)

    def put(self, run: ReviewRun) -> ReviewRun:
        """Записать версию прогона, как её закоммитил бы воркер (без проверки перехода)."""
        self.uow.review_runs._by_id[run.id] = run
        return run

    def end_session(self) -> None:
        for session in self.uow.sessions.stored:
            self.uow.sessions.delete(session.id)


class Ticks:
    """Ожидание опроса: часы делают шаг, на шаге `n` выполняется запланированное."""

    def __init__(self, plan: dict[int, Callable[[], object]]) -> None:
        self.plan = plan
        self.now = 0.0
        self.tick = 0

    async def sleep(self, seconds: float) -> None:
        self.tick += 1
        self.now += seconds
        action = self.plan.get(self.tick)
        if action is not None:
            action()

    def clock(self) -> float:
        return self.now


def _app(world: World, ticks: Ticks, catalog: FakeInstallationCatalog | None = None) -> tuple[FastAPI, dict[str, str]]:
    app = create_app(SETTINGS)
    cookies = sign_in_with_session(app, world.uow)
    app.dependency_overrides[get_untraced_unit_of_work_factory] = lambda: lambda: world.uow
    app.dependency_overrides[get_installation_catalog] = lambda: catalog or FakeInstallationCatalog(
        [VISIBLE, HIDDEN], managers={(VISIBLE.full_name, "octocat")}
    )
    app.dependency_overrides[get_sleep] = lambda: ticks.sleep
    app.dependency_overrides[get_clock] = lambda: ticks.clock
    return app, cookies


def _stream(world: World, plan: dict[int, Callable[[], object]]) -> str:
    """Тело потока до закрытия; сессия кончается на шаге 5, поток — на проверке после него."""
    ticks = Ticks({**plan, 5: _then(plan.get(5), world.end_session)})
    app, cookies = _app(world, ticks)
    response = TestClient(app, cookies=cookies).get(PATH)
    assert response.status_code == 200
    text: str = response.text
    return text


def _then(first: Callable[[], object] | None, second: Callable[[], object]) -> Callable[[], object]:
    def both() -> None:
        if first is not None:
            first()
        second()

    return both


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    """События текста SSE: имя и разобранные данные; строку `retry` пропускаем."""
    events = []
    for block in text.split("\n\n"):
        if not block or block.startswith("retry:"):
            continue
        name_line, data_line = block.split("\n")
        assert name_line.startswith("event: ") and data_line.startswith("data: ")
        events.append((name_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))))
    return events


def test_without_a_session_the_stream_is_401() -> None:
    response = TestClient(create_app(SETTINGS)).get(PATH)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_a_provider_failure_at_connect_is_502_and_no_stream_opens() -> None:
    world = World()
    app, cookies = _app(world, Ticks({}), FakeInstallationCatalog(error=VcsError("GitHub недоступен")))

    response = TestClient(app, cookies=cookies).get(PATH)

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "SCM_UNAVAILABLE"


def test_the_stream_starts_with_retry_and_forbids_caching_and_buffering() -> None:
    world = World()
    ticks = Ticks({1: world.end_session})
    app, cookies = _app(world, ticks)

    response = TestClient(app, cookies=cookies).get(PATH)

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.startswith("retry: 3000\n\n")


def test_three_event_types_with_list_rows() -> None:
    world = World()
    created = _run(world.visible_mr, S.QUEUED, FUTURE, created_at=FUTURE)
    later = FUTURE + timedelta(seconds=1)
    world.uow.findings.add(
        Finding(
            id=new_id(),
            review_run_id=created.id,
            anchor=DiffAnchor(file_path="app.py", side=DiffSide.NEW, old_line=None, new_line=3),
            category=FindingCategory.CORRECTNESS,
            severity=FindingSeverity.HIGH,
            message="Ошибка",
            created_at=FUTURE,
            updated_at=FUTURE,
        )
    )

    text = _stream(
        world,
        {
            1: lambda: world.put(created),
            2: lambda: world.put(replace(created, status=S.ANALYSING, updated_at=later)),
            3: lambda: world.put(
                replace(created, status=S.COMPLETED, updated_at=later + timedelta(seconds=1), duration_seconds=12.5)
            ),
        },
    )

    events = _events(text)
    assert [name for name, _ in events] == ["run.created", "run.status_changed", "run.finished"]
    assert [data["status"] for _, data in events] == ["queued", "analysing", "completed"]
    finished = events[-1][1]
    assert finished["id"] == str(created.id)
    assert finished["duration_seconds"] == 12.5
    assert finished["findings_count"] == 1
    assert finished["merge_request"]["repository"]["id"] == str(world.visible.id)
    assert finished["merge_request"]["url"] == "https://github.com/octo-org/api/pull/42"
    assert finished["updated_at"].endswith("Z")


def test_runs_of_a_repository_the_user_cannot_see_do_not_arrive() -> None:
    world = World()
    hidden = _run(world.hidden_mr, S.QUEUED, FUTURE, created_at=FUTURE)
    visible = _run(world.visible_mr, S.QUEUED, FUTURE, created_at=FUTURE)

    def commit_both() -> None:
        world.put(hidden)
        world.put(visible)

    text = _stream(world, {1: commit_both})

    assert [data["id"] for _, data in _events(text)] == [str(visible.id)]


def test_history_before_connecting_is_not_sent() -> None:
    world = World()
    world.put(_run(world.visible_mr, S.COMPLETED, PAST))
    # Изменён за миг до подключения — его версию клиент возьмёт снимком REST.
    world.put(_run(world.visible_mr, S.ANALYSING, datetime.now(UTC) - timedelta(seconds=1)))

    text = _stream(world, {})

    assert _events(text) == []


@pytest.mark.parametrize("smaller_id_first", [True, False])
def test_the_cursor_does_not_lose_a_run_with_the_same_updated_at(smaller_id_first: bool) -> None:
    """Два прогона с одним updated_at, второй закоммичен опросом позже — в любом порядке id."""
    world = World()
    first, second = sorted(
        [_run(world.visible_mr, S.ANALYSING, FUTURE), _run(world.visible_mr, S.ANALYSING, FUTURE)],
        key=lambda r: r.id,
        reverse=not smaller_id_first,
    )

    text = _stream(world, {1: lambda: world.put(first), 2: lambda: world.put(second)})

    assert [data["id"] for _, data in _events(text)] == [str(first.id), str(second.id)]


def test_an_expired_session_closes_the_stream() -> None:
    world = World()
    ticks = Ticks({2: world.end_session})
    app, cookies = _app(world, ticks)

    response = TestClient(app, cookies=cookies).get(PATH)

    assert response.text == "retry: 3000\n\n"
    # Проверка сессии — раз в 5 секунд: поток закрыт на первой проверке после выхода.
    assert ticks.now == 5


def test_silence_gets_a_ping_at_least_every_fifteen_seconds() -> None:
    world = World()
    ticks = Ticks({15: world.end_session})
    app, cookies = _app(world, ticks)

    response = TestClient(app, cookies=cookies).get(PATH)

    assert response.text == "retry: 3000\n\nevent: ping\ndata: {}\n\n"


# --- поток в реальном времени и телеметрия ---------------------------------------


def test_events_reach_the_client_as_they_happen_and_a_client_close_is_not_an_error(
    spans: InMemorySpanExporter,
) -> None:
    """Событие уходит, пока поток открыт; уход клиента — штатный конец, а не ERROR спана.

    ASGI-приложение гоняется напрямую: `TestClient` собирает тело целиком. `spec_version`
    2.3 — как у uvicorn: уход клиента Starlette узнаёт из `http.disconnect`.
    """
    world = World()
    run = world.put(_run(world.visible_mr, S.ANALYSING, PAST))
    release = asyncio.Event()
    gone = asyncio.Event()

    async def held_sleep(seconds: float) -> None:
        await release.wait()
        release.clear()

    app, cookies = _app(world, Ticks({}))
    app.dependency_overrides[get_sleep] = lambda: held_sleep
    cookie = "; ".join(f"{name}={value}" for name, value in cookies.items())
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": PATH,
        "raw_path": PATH.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"cookie", cookie.encode())],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    sent: list[Message] = []
    changed = asyncio.Event()

    async def receive() -> Message:
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)
        if b"event: run.finished" in message.get("body", b""):
            changed.set()

    async def scenario() -> None:
        stream = asyncio.create_task(app(scope, receive, send))
        await asyncio.sleep(0.05)
        world.put(replace(run, status=S.COMPLETED, updated_at=FUTURE))
        release.set()
        await asyncio.wait_for(changed.wait(), timeout=5)
        # Событие у клиента, поток открыт, запрос не закончен.
        assert sent[-1].get("more_body") is True
        assert not [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]
        gone.set()
        await asyncio.wait_for(stream, timeout=5)

    asyncio.run(scenario())

    (server,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]
    assert server.status.status_code is StatusCode.UNSET
    assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}


def test_the_untraced_factory_is_wired_to_the_container() -> None:
    uow = FakeUnitOfWork()

    class Container:
        def untraced_unit_of_work(self) -> FakeUnitOfWork:
            return uow

    factory = get_untraced_unit_of_work_factory(Container())  # type: ignore[arg-type]

    assert factory() is uow
