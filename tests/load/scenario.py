"""Нагрузочный сценарий конвейера ревью на сквозном стенде.

API - настоящий ASGI-сервер uvicorn на свободном порту loopback, вебхуки шлёт
пул потоков по HTTP. Каждый воркер - отдельный процесс (`spawn`) со своими
движком базы, соединением с брокером и боевым consume-циклом `Worker.run`, как
в проде: разбор диффа и промптов в воркерах не делит GIL с приёмом, и время
ответа на вебхук - время API, а не очередь к интерпретатору.
База, брокер и весь код между ними - настоящие. Внешние системы - `FakeGitHub`
и заглушка модели - отвечают с заданной задержкой: их латентность внешняя,
сценарий меряет то, за что отвечает сервис. `FakeGitHub` живёт в процессе
сценария (там его состояние и проверки), воркеры ходят к нему по HTTP на loopback.

Повторные доставки идут вперемешку с первыми и параллельно с ними: так
проверяется идемпотентность приёма под конкуренцией, а не только по очереди.

Метрики - чистые функции над записанными замерами (`summarize`).
"""

import json
import logging
import math
import multiprocessing
import queue
import random
import threading
import time
import traceback
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise
from multiprocessing.synchronize import Event
from typing import Any, Self

import httpx
import uvicorn
from pydantic import BaseModel
from sqlalchemy import create_engine

from app.application.ports.llm_gateway import LlmRequest, LlmResponse
from app.config import Settings
from app.infrastructure.llm.stub import StubLlmGateway
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.infrastructure.queue.rabbitmq import DLQ_NAME
from app.worker.factory import (
    Worker,
    build_review_handler,
    build_worker,
    context_limits,
    llm_limits,
    load_checked_prompt_texts,
)

from ..e2e.stack import (
    WEBHOOK_PATH,
    Stack,
    process_container,
    webhook_body,
    webhook_headers,
)
from ..fakes.github_api import API_URL, FakeGitHub

BASE_SHA = "ba5e" * 10
RULES_MD = (
    "## Error Handling Standards\n\n- pattern: `app/**/*.py`\n\n"
    "1. Отказ возвращается значением Result, а не исключением\n"
)
SOURCE = (
    "from decimal import Decimal\n\n\n"
    "def charge(amount: Decimal, balance: Decimal) -> Decimal:\n"
    "    if amount > balance:\n"
    '        raise ValueError("недостаточно средств")\n'
    "    return balance - amount\n"
)


@dataclass(frozen=True, slots=True)
class LoadProfile:
    pull_requests: int = 50
    # Повторных доставок уже отправленных событий - с тем же X-GitHub-Delivery.
    redeliveries: int = 10
    senders: int = 16
    workers: int = 4
    model_latency_seconds: float = 0.0
    github_latency_seconds: float = 0.0
    timeout_seconds: float = 120.0
    seed: int = 268
    # Воркеры стартуют после приёма: каждый повтор приходит при активном прогоне,
    # и любой лишний прогон - нарушение, а не повтор после завершения.
    hold_workers_until_intake_done: bool = False


@dataclass(frozen=True, slots=True)
class Delivery:
    pr: int
    redelivery: bool
    status: int
    seconds: float
    run_id: str | None


@dataclass(frozen=True, slots=True)
class RunTiming:
    pr: int
    status: str
    created_at: datetime
    finished_at: datetime
    processing_seconds: float | None
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class LoadResult:
    profile: LoadProfile
    deliveries: tuple[Delivery, ...]
    runs: tuple[RunTiming, ...]
    reviews_per_pr: dict[int, int]
    unfinished_runs: int
    queue_left: int
    dlq_left: int
    intake_seconds: float
    drain_seconds: float


class SlowStubLlm:
    """Заглушка модели с задержкой ответа: латентность провайдера без провайдера."""

    def __init__(self, latency_seconds: float) -> None:
        self._latency = latency_seconds
        self._stub = StubLlmGateway()

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        if self._latency:
            time.sleep(self._latency)
        return self._stub.complete(request)


# --- метрики ---------------------------------------------------------------


def percentile(values: Sequence[float], q: float) -> float | None:
    """Перцентиль методом ближайшего ранга: значение из выборки, без интерполяции."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(result: LoadResult) -> dict[str, Any]:
    deliveries = result.deliveries
    latencies = [d.seconds for d in deliveries]
    end_to_end = [(r.finished_at - r.created_at).total_seconds() for r in result.runs]
    timings = {r.run_id: r for r in result.runs if r.run_id}
    runs_per_pr: dict[int, set[str]] = {}
    for d in deliveries:
        if d.run_id is not None:
            runs_per_pr.setdefault(d.pr, set()).add(d.run_id)
    extra_after_completion: set[int] = set()
    violations: set[int] = set()
    for pr, run_ids in runs_per_pr.items():
        # Исходный прогон - самый ранний по `created_at`, а не прогон исходной доставки:
        # план перемешан, и повтор может уйти раньше неё. Тогда исходная доставка,
        # пришедшая после завершения его прогона, законно создаёт второй.
        timed = sorted((timings[r] for r in run_ids if r in timings), key=lambda t: t.created_at)
        if len(timed) < len(run_ids):
            # Без времени прогона не доказать, что прежний уже завершился: второй - нарушение.
            if len(run_ids) > 1:
                violations.add(pr)
            continue
        # Спека разрешает новый прогон, только если прежний уже был терминальным.
        for previous, later in pairwise(timed):
            if later.created_at >= previous.finished_at:
                extra_after_completion.add(pr)
            else:
                violations.add(pr)
    return {
        "deliveries": len(deliveries),
        "status_codes": dict(sorted(Counter(d.status for d in deliveries).items())),
        "server_errors": sum(1 for d in deliveries if d.status >= 500),
        "intake_latency_seconds": {f"p{q}": percentile(latencies, q) for q in (50, 95, 99)}
        | {"max": max(latencies, default=None)},
        "intake_throughput_per_second": (
            len(deliveries) / result.intake_seconds if result.intake_seconds else None
        ),
        "end_to_end_seconds": {f"p{q}": percentile(end_to_end, q) for q in (50, 95, 99)}
        | {"max": max(end_to_end, default=None)},
        "drain_seconds": result.drain_seconds,
        "runs_by_status": dict(sorted(Counter(r.status for r in result.runs).items())),
        "pull_requests_with_extra_runs": sorted(violations),
        # Лишний прогон после завершения прежнего - новый прогон и второе ревью (пробел R-4
        # TEST_PLAN). Его может создать и исходная доставка, если повтор ушёл раньше неё.
        "extra_runs_after_completion": sorted(extra_after_completion),
        "pull_requests_without_one_review_per_run": sorted(
            pr
            for pr in range(1, result.profile.pull_requests + 1)
            if result.reviews_per_pr.get(pr, 0) != max(1, len(runs_per_pr.get(pr, ())))
        ),
        "unfinished_runs": result.unfinished_runs,
        "queue_left": result.queue_left,
        "dlq_left": result.dlq_left,
    }


# --- прогон ----------------------------------------------------------------


class _Server:
    """uvicorn в фоновом потоке на свободном порту loopback."""

    def __init__(self, app: Any) -> None:
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning", access_log=False
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="load-api", daemon=True)

    def __enter__(self) -> str:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("uvicorn не поднялся за 10 с")
            time.sleep(0.01)
        (socket,) = self._server.servers[0].sockets
        host, port = socket.getsockname()[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(10)


class _GitHubOverHttp:
    """`FakeGitHub` по HTTP на свободном порту loopback - для воркеров в других процессах.

    Состояние фейка (PR, ревью, задержка ответа) остаётся в процессе сценария:
    сервер переводит запрос в `httpx.Request` для `FakeGitHub.handle` и отдаёт
    его ответ. Поток на соединение: задержка ответа одному воркеру не держит
    остальных.
    """

    def __init__(self, github: FakeGitHub) -> None:
        handle = github.handle

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def proxy(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                headers = [(k, v) for k, v in self.headers.items() if k.lower() != "host"]
                request = httpx.Request(self.command, API_URL + self.path, headers=headers, content=body)
                response = handle(request)
                content = response.read()
                self.send_response(response.status_code)
                for name, value in response.headers.multi_items():
                    if name.lower() not in _HOP_HEADERS:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = proxy

            def log_message(self, *_: Any) -> None:
                """Строка на запрос в stderr прогону не нужна."""

        self._server = _GitHubHttpServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="load-github", daemon=True
        )

    def __enter__(self) -> str:
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(10)


class _GitHubHttpServer(ThreadingHTTPServer):
    # Очередь `listen` по умолчанию - 5: воркеры открывают соединения разом, лишние ждали бы
    # повтора SYN.
    request_queue_size = 128


# Длину тела сервер ставит сам: ответ фейка уже прочитан целиком.
_HOP_HEADERS = frozenset({"content-length", "transfer-encoding", "connection", "keep-alive"})


@dataclass(frozen=True, slots=True)
class _WorkerSpec:
    """Всё, из чего процесс воркера собирает себя; переезжает в процесс через pickle."""

    settings: Settings
    # С паролем: `settings.database_url` стенда собран из `str(engine.url)`, где он скрыт.
    database_url: str
    rabbitmq_url: str
    github_url: str
    model_latency_seconds: float


# Запуск процесса с импортом приложения и сборкой контейнера; больше - зависание.
WORKER_READY_SECONDS = 60.0
# Сколько ждём выхода процесса после отмены консьюмера, потом - terminate.
WORKER_STOP_SECONDS = 10.0


def _worker_process(
    spec: _WorkerSpec, index: int, go: Event, stop: Event, events: Any
) -> None:
    """Точка входа процесса воркера: свои движок, контейнер, соединение с брокером.

    Сообщает в `events` готовность `(index, None)` или сбой `(index, traceback)`,
    ждёт `go` и крутит `Worker.run`, пока `stop` не отменит консьюмера.
    """
    try:
        # `Worker.run` в finally отменяет консьюмера ещё раз, и pika предупреждает
        # о повторной отмене: для остановки снаружи это штатно, а не сбой.
        logging.getLogger("pika.adapters.blocking_connection").setLevel(logging.ERROR)
        github = httpx.Client(base_url=spec.github_url, timeout=30)
        container = process_container(
            spec.settings,
            create_engine(spec.database_url),
            spec.rabbitmq_url,
            github,
            SlowStubLlm(spec.model_latency_seconds),
        )
        handler = build_review_handler(
            container,
            context_limits(spec.settings),
            llm_limits(spec.settings),
            load_checked_prompt_texts(spec.settings, DEFAULT_PROMPTS_DIR),
        )
        worker = build_worker(spec.settings)
        events.put((index, None))
        while not go.wait(0.1):
            if stop.is_set():
                return
        threading.Thread(target=_cancel_on, args=(stop, worker), daemon=True).start()
        worker.run(handler)
    except BaseException:  # noqa: BLE001 — переносится в процесс сценария текстом
        events.put((index, traceback.format_exc()))


def _cancel_on(stop: Event, worker: Worker) -> None:
    """Отмена консьюмера по `stop`; из чужого потока - только `add_callback_threadsafe`.

    `BlockingConnection` не потокобезопасен, и это единственный вызов, который
    можно делать снаружи.
    """
    stop.wait()
    worker.connection.add_callback_threadsafe(_canceller(worker))


class _Workers:
    """Процессы воркеров с боевым consume-циклом; останавливаются отменой консьюмера.

    `__enter__` запускает процессы и ждёт, пока каждый соберёт контейнер и
    подключится к брокеру: запуск интерпретатора не попадает в замеры.
    Сообщения они берут после `start()`. Ошибки процессов - текстом в `errors`.
    """

    def __init__(self, spec: _WorkerSpec, count: int) -> None:
        # `spawn` явно: одинаково на macOS и Linux, без унаследованного состояния pytest и потоков.
        context = multiprocessing.get_context("spawn")
        self._go = context.Event()
        self._stop = context.Event()
        self._events = context.Queue()
        self._processes = [
            context.Process(
                target=_worker_process,
                args=(spec, index, self._go, self._stop, self._events),
                name=f"load-worker-{index}",
                daemon=True,
            )
            for index in range(count)
        ]
        self.errors: list[str] = []

    def __enter__(self) -> Self:
        try:
            for process in self._processes:
                process.start()
            self._await_ready()
        except BaseException:
            self.__exit__()
            raise
        return self

    def _await_ready(self) -> None:
        deadline = time.monotonic() + WORKER_READY_SECONDS
        ready = 0
        while ready < len(self._processes):
            try:
                index, error = self._events.get(timeout=0.5)
            except queue.Empty:
                if dead := [p.name for p in self._processes if p.exitcode is not None]:
                    raise RuntimeError(f"процессы воркеров завершились до готовности: {dead}") from None
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"воркеры не поднялись за {WORKER_READY_SECONDS:g} с: "
                        f"готовы {ready} из {len(self._processes)}"
                    ) from None
                continue
            if error is not None:
                raise RuntimeError(f"load-worker-{index} упал при запуске:\n{error}")
            ready += 1

    def start(self) -> None:
        self._go.set()

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        for process in self._processes:
            # Запуск мог упасть на середине: `join` незапущенного процесса подменил бы исходную ошибку.
            if process.pid is None:
                continue
            process.join(WORKER_STOP_SECONDS)
            if process.is_alive():
                process.terminate()
                process.join()
                self.errors.append(f"{process.name} не остановился за {WORKER_STOP_SECONDS:g} с")
            elif process.exitcode:
                self.errors.append(f"{process.name} завершился с кодом {process.exitcode}")
        while True:
            try:
                index, error = self._events.get_nowait()
            except queue.Empty:
                break
            if error is not None:
                self.errors.append(f"load-worker-{index}:\n{error}")


def _canceller(worker: Worker) -> Callable[[], None]:
    """Отмена консьюмера для потока соединения; число возвращённых сообщений не нужно."""

    def cancel() -> None:
        worker.channel.cancel()

    return cancel


def _deliveries_plan(profile: LoadProfile) -> list[tuple[int, bool]]:
    """Номера PR в порядке отправки: каждый один раз плюс повторы, вперемешку."""
    rng = random.Random(profile.seed)
    plan = [(pr, False) for pr in range(1, profile.pull_requests + 1)]
    plan += [(rng.randint(1, profile.pull_requests), True) for _ in range(profile.redeliveries)]
    rng.shuffle(plan)
    return plan


def run_load(stack: Stack, profile: LoadProfile) -> LoadResult:
    pulls = {
        pr: stack.github.open_pull(
            pr,
            base_sha=BASE_SHA,
            head_sha=f"{pr:040x}",
            base_files={".review/rules.md": RULES_MD},
            head_files={f"app/payments_{pr}.py": SOURCE},
            title=f"feat: списание {pr}",
        )
        for pr in range(1, profile.pull_requests + 1)
    }
    stack.github.latency_seconds = profile.github_latency_seconds

    with (
        _Server(stack.api()) as base_url,
        _GitHubOverHttp(stack.github) as github_url,
        httpx.Client(base_url=base_url, timeout=30) as client,
    ):

        def send(item: tuple[int, bool]) -> Delivery:
            pr, redelivery = item
            body = webhook_body(pulls[pr])
            started = time.perf_counter()
            response = client.post(
                WEBHOOK_PATH, content=body, headers=webhook_headers(body, f"load-{pr}")
            )
            seconds = time.perf_counter() - started
            run_id = response.json().get("id") if response.status_code == 202 else None
            return Delivery(pr, redelivery, response.status_code, seconds, run_id)

        spec = _WorkerSpec(
            settings=stack.settings,
            database_url=stack.engine.url.render_as_string(hide_password=False),
            rabbitmq_url=stack.rabbitmq_url,
            github_url=github_url,
            model_latency_seconds=profile.model_latency_seconds,
        )
        with _Workers(spec, profile.workers) as workers:
            if not profile.hold_workers_until_intake_done:
                workers.start()
            started = time.perf_counter()
            with ThreadPoolExecutor(profile.senders) as pool:
                deliveries = tuple(pool.map(send, _deliveries_plan(profile)))
            intake_seconds = time.perf_counter() - started
            if profile.hold_workers_until_intake_done:
                workers.start()

            deadline = time.monotonic() + profile.timeout_seconds
            while time.monotonic() < deadline and stack.unfinished_runs():
                time.sleep(0.2)
            drain_seconds = time.perf_counter() - started
        if workers.errors:
            raise RuntimeError("воркеры нагрузочного прогона упали:\n" + "\n".join(workers.errors))

    runs = []
    for run_id in sorted({d.run_id for d in deliveries if d.run_id is not None}):
        run = stack.run(run_id)
        pr = next(d.pr for d in deliveries if d.run_id == run_id)
        runs.append(
            RunTiming(
                pr=pr,
                status=run.status.value,
                created_at=run.created_at,
                # Терминальный прогон больше не меняется: его updated_at - момент завершения.
                finished_at=run.updated_at,
                processing_seconds=run.duration_seconds,
                run_id=run_id,
            )
        )
    return LoadResult(
        profile=profile,
        deliveries=deliveries,
        runs=tuple(runs),
        reviews_per_pr={pr: len(stack.github.reviews_of(pr)) for pr in pulls},
        unfinished_runs=stack.unfinished_runs(),
        queue_left=stack.queue_depth(),
        dlq_left=stack.queue_depth(DLQ_NAME),
        intake_seconds=intake_seconds,
        drain_seconds=drain_seconds,
    )


def report_json(result: LoadResult) -> str:
    return json.dumps(
        {"profile": asdict(result.profile), "summary": summarize(result)},
        ensure_ascii=False,
        indent=2,
    )


def report_markdown(result: LoadResult) -> str:
    summary = summarize(result)
    p = result.profile

    def seconds(value: float | None) -> str:
        return "—" if value is None else f"{value * 1000:.0f} мс"

    intake, e2e = summary["intake_latency_seconds"], summary["end_to_end_seconds"]
    throughput = summary["intake_throughput_per_second"]
    lines = [
        "# Нагрузочный прогон конвейера ревью",
        "",
        (
            f"PR: {p.pull_requests}, повторных доставок: {p.redeliveries}, "
            f"отправителей: {p.senders}, воркеров: {p.workers}."
        ),
        f"Задержка модели: {p.model_latency_seconds:g} с, задержка GitHub: {p.github_latency_seconds:g} с.",
        "",
        "| Метрика | p50 | p95 | p99 | max |",
        "|---|---|---|---|---|",
        "| Ответ на вебхук | "
        + " | ".join(seconds(intake[k]) for k in ("p50", "p95", "p99", "max"))
        + " |",
        "| От приёма до завершения прогона | "
        + " | ".join(seconds(e2e[k]) for k in ("p50", "p95", "p99", "max"))
        + " |",
        "",
        f"- Пропускная способность приёма: {throughput:.1f} вебхуков/с." if throughput else None,
        f"- Очередь разобрана за {result.drain_seconds:.1f} с от первой доставки.",
        f"- Коды ответов: {summary['status_codes']}; ответов 5xx: {summary['server_errors']}.",
        f"- Прогоны по статусам: {summary['runs_by_status']}; незавершённых: {summary['unfinished_runs']}.",
        f"- PR с лишним прогоном при незавершённом прежнем: {summary['pull_requests_with_extra_runs'] or 'нет'}.",
        (
            "- Лишние прогоны после завершения прежнего (новый прогон и второе ревью, R-4): "
            f"{summary['extra_runs_after_completion'] or 'нет'}."
        ),
        f"- PR, где ревью не по одному на прогон: {summary['pull_requests_without_one_review_per_run'] or 'нет'}.",
        f"- Осталось в очереди: {summary['queue_left']}, в DLQ: {summary['dlq_left']}.",
    ]
    return "\n".join(line for line in lines if line is not None) + "\n"
