"""Нагрузочный сценарий конвейера ревью на сквозном стенде.

API - настоящий ASGI-сервер uvicorn на свободном порту loopback, вебхуки шлёт
пул потоков по HTTP. Каждый воркер - свой поток со своим соединением с
брокером и боевым consume-циклом `Worker.run`, как отдельный процесс в проде.
База, брокер и весь код между ними - настоящие. Внешние системы - `FakeGitHub`
и заглушка модели - отвечают с заданной задержкой: их латентность внешняя,
сценарий меряет то, за что отвечает сервис.

Повторные доставки идут вперемешку с первыми и параллельно с ними: так
проверяется идемпотентность приёма под конкуренцией, а не только по очереди.

Метрики - чистые функции над записанными замерами (`summarize`).
"""

import json
import logging
import math
import random
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Self

import httpx
import uvicorn
from pydantic import BaseModel

from app.application.ports.llm_gateway import LlmRequest, LlmResponse
from app.infrastructure.llm.stub import StubLlmGateway
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.infrastructure.queue.rabbitmq import DLQ_NAME
from app.worker.factory import (
    Worker,
    build_message_handler,
    build_review_handler,
    build_worker,
    context_limits,
    llm_limits,
    load_checked_prompt_texts,
)

from ..e2e.stack import WEBHOOK_PATH, Stack, webhook_body, webhook_headers

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
    timings = {r.run_id: r for r in result.runs}
    first_run = {d.pr: d.run_id for d in deliveries if not d.redelivery and d.run_id}
    runs_per_pr: dict[int, set[str]] = {}
    for d in deliveries:
        if d.run_id is not None:
            runs_per_pr.setdefault(d.pr, set()).add(d.run_id)
    after_completion: set[int] = set()
    violations: set[int] = set()
    for d in deliveries:
        original = first_run.get(d.pr)
        if not d.redelivery or d.run_id is None or d.run_id == original:
            continue
        new, old = timings.get(d.run_id), timings.get(original or "")
        # Спека разрешает новый прогон, только если прежний уже был терминальным.
        if new is not None and old is not None and new.created_at >= old.finished_at:
            after_completion.add(d.pr)
        else:
            violations.add(d.pr)
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
        # Повтор после завершения - новый прогон и второе ревью (пробел R-4 TEST_PLAN).
        "redeliveries_after_completion": sorted(after_completion),
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


class _Workers:
    """Потоки воркеров с боевым consume-циклом; останавливаются отменой консьюмера.

    `Worker.run(limit=None)` крутится, пока консьюмер не отменён. Отмена из
    чужого потока идёт через `add_callback_threadsafe`: `BlockingConnection`
    не потокобезопасен, и это единственный вызов, который можно делать снаружи.
    """

    def __init__(self, stack: Stack, count: int, model_latency_seconds: float) -> None:
        texts = load_checked_prompt_texts(stack.settings, DEFAULT_PROMPTS_DIR)
        self._workers: list[Worker] = []
        self._threads: list[threading.Thread] = []
        self._started = False
        self.errors: list[BaseException] = []
        for index in range(count):
            container = stack.container(SlowStubLlm(model_latency_seconds))
            handler = build_message_handler(
                build_review_handler(
                    container, context_limits(stack.settings), llm_limits(stack.settings), texts
                )
            )
            worker = build_worker(stack.settings)
            self._workers.append(worker)
            self._threads.append(
                threading.Thread(
                    target=self._run,
                    args=(worker, handler),
                    name=f"load-worker-{index}",
                    daemon=True,
                )
            )

    def _run(self, worker: Worker, handler: Any) -> None:
        try:
            worker.run(handler)
        except BaseException as error:  # noqa: BLE001 — переносится в поток сценария
            self.errors.append(error)

    def __enter__(self) -> Self:
        self._started = True
        for thread in self._threads:
            thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        # Отправка могла упасть до старта: `join` незапущенного потока подменил бы исходную ошибку.
        if not self._started:
            return
        # `Worker.run` в finally отменяет консьюмера ещё раз, и pika предупреждает
        # о повторной отмене: для остановки снаружи это штатно, а не сбой.
        pika_log = logging.getLogger("pika.adapters.blocking_connection")
        level = pika_log.level
        pika_log.setLevel(logging.ERROR)
        try:
            for worker in self._workers:
                worker.connection.add_callback_threadsafe(_canceller(worker))
            for thread in self._threads:
                thread.join(10)
        finally:
            pika_log.setLevel(level)


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

    with _Server(stack.api()) as base_url, httpx.Client(base_url=base_url, timeout=30) as client:

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

        workers = _Workers(stack, profile.workers, profile.model_latency_seconds)
        if not profile.hold_workers_until_intake_done:
            workers.__enter__()
        try:
            started = time.perf_counter()
            with ThreadPoolExecutor(profile.senders) as pool:
                deliveries = tuple(pool.map(send, _deliveries_plan(profile)))
            intake_seconds = time.perf_counter() - started
            if profile.hold_workers_until_intake_done:
                workers.__enter__()

            deadline = time.monotonic() + profile.timeout_seconds
            while time.monotonic() < deadline and stack.unfinished_runs():
                time.sleep(0.2)
            drain_seconds = time.perf_counter() - started
        finally:
            workers.__exit__()
        if workers.errors:
            raise workers.errors[0]

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
        f"- PR с лишним прогоном: {summary['pull_requests_with_extra_runs'] or 'нет'}.",
        (
            "- Повторы после завершения (новый прогон и второе ревью, R-4): "
            f"{summary['redeliveries_after_completion'] or 'нет'}."
        ),
        f"- PR, где ревью не по одному на прогон: {summary['pull_requests_without_one_review_per_run'] or 'нет'}.",
        f"- Осталось в очереди: {summary['queue_left']}, в DLQ: {summary['dlq_left']}.",
    ]
    return "\n".join(line for line in lines if line is not None) + "\n"
