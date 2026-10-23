"""Собирает consume-цикл воркера и доменный обработчик, который в нём крутится.

`Worker`/`build_worker` не знают о доменном пайплайне: принимают обработчик
сообщением-байтами. `build_review_handler` — сборка настоящего обработчика
(разбор сообщения + `run_review`), а `run_worker` — то, что вызывает
`app/worker/__main__.py`, чтобы не импортировать `app.infrastructure`/
`app.application` напрямую (точка входа обязана оставаться тонкой).
Ack/nack-решение — чистая `should_ack`, здесь только вызовы канала.
"""

import contextlib
import logging
import signal
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Protocol

import pika
from opentelemetry import trace

from app.application.context_assembly import ContextLimits
from app.application.llm_review import LlmLimits
from app.application.ports import (
    CacheStore,
    DefinitionExtractor,
    UnitOfWork,
    VcsGateway,
)
from app.application.ports.llm_gateway import LlmGateway
from app.application.ports.webhook_deliveries import WebhookDeliveries
from app.application.review_pipeline import run_review
from app.application.session_purge import purge_expired_sessions
from app.application.stale_sweep import sweep_stale_runs
from app.application.webhook_redelivery import redeliver_failed_webhooks
from app.config import Settings
from app.domain.ids import new_id
from app.domain.prompt import PromptTexts, check_window_config
from app.domain.webhook_redelivery import RedeliveryPolicy
from app.infrastructure.container import build_container
from app.infrastructure.logging import configure_logging
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR, load_prompt_texts
from app.infrastructure.queue.rabbitmq import (
    QUEUE_NAME,
    consuming_span,
    declare_topology,
    from_wire_message,
)
from app.infrastructure.telemetry import (
    REVIEW_RUN_ID,
    WORKER_SERVICE,
    OtelStageTracer,
    configure_telemetry,
    instrument_engine,
    record_current_exception,
    shutdown_tracing,
    sweep_span,
)
from app.worker.handling import should_ack

logger = logging.getLogger(__name__)

MessageHandler = Callable[[bytes], None]

# Как часто выметать зависшие прогоны. Проход дёргается на каждом такте
# consume-цикла (простой раз в секунду или очередное сообщение) и сам
# ограничивается этим интервалом.
SWEEP_INTERVAL_SECONDS = 60.0

# Сигналы штатной остановки: SIGTERM шлёт `docker stop`, SIGINT — Ctrl+C.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


@dataclass(slots=True)
class StopRequest:
    """Просьба остановить воркер: выставляет обработчик сигнала, читает consume-цикл."""

    requested: bool = False


@contextlib.contextmanager
def stop_on_signals() -> Iterator[StopRequest]:
    """На время блока SIGTERM и SIGINT не убивают процесс, а просят цикл остановиться.

    Обработчик Python выполняет в главном потоке между байткодами — посреди обработки
    сообщения или ожидания брокера, — поэтому он только ставит флаг и возвращает прежние
    обработчики: второй сигнал действует как обычно (SIGTERM завершает процесс сразу,
    SIGINT бросает `KeyboardInterrupt`). После блока прежние обработчики возвращаются.
    """
    stop = StopRequest()
    previous = {signum: signal.getsignal(signum) for signum in STOP_SIGNALS}

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop.requested = True
        for restored, handler in previous.items():
            signal.signal(restored, handler)

    for signum in STOP_SIGNALS:
        signal.signal(signum, request_stop)
    try:
        yield stop
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@dataclass(frozen=True, slots=True)
class Worker:
    connection: pika.BlockingConnection
    channel: pika.adapters.blocking_connection.BlockingChannel

    def run(
        self,
        handler: MessageHandler,
        *,
        limit: int | None = None,
        sweep: Callable[[], object] | None = None,
        sweep_interval: float = SWEEP_INTERVAL_SECONDS,
        stop: StopRequest | None = None,
    ) -> None:
        """Потреблять `review_jobs`.

        `limit=None` — бесконечно (боевой запуск `python -m app.worker`);
        конечное число — для тестов, чтобы не держать процесс висящим на
        пустой очереди. В любом случае при выходе отменяем регистрацию
        консьюмера и закрываем соединение — иначе брокер продолжает считать
        канал живым консьюмером и делит с ним следующие сообщения, хотя
        Python-цикл их уже не читает.

        `sweep` — периодический проход по зависшим прогонам; его сбой
        логируется и не останавливает потребление.

        `stop` — просьба остановиться (`stop_on_signals`). Сообщение в работе
        дорабатывает и подтверждается, следующее не берётся, даже если брокер
        уже выдал его циклу. На простое цикл выходит на ближайшем такте, не
        позже `inactivity_timeout`. В `finally` `cancel()` возвращает в очередь
        только сообщения из буфера pika (`pending_events`); уже выданное циклу,
        но не подтверждённое брокер вернёт при `connection.close()`.
        """
        processed = 0
        last_sweep: float | None = None
        try:
            for method, properties, body in self.channel.consume(
                QUEUE_NAME, inactivity_timeout=1
            ):
                # Выданное, но не подтверждённое сообщение брокер вернёт в
                # очередь при `connection.close()` в `finally`.
                if stop is not None and stop.requested:
                    break
                if sweep is not None and (
                    last_sweep is None or time.monotonic() - last_sweep >= sweep_interval
                ):
                    last_sweep = time.monotonic()
                    try:
                        with sweep_span():
                            sweep()
                    except Exception:
                        logger.exception("проход по зависшим прогонам завершился ошибкой")
                    # Сигнал мог прийти посреди прохода — сообщение этого такта не берём.
                    if stop is not None and stop.requested:
                        break
                if method is None:
                    continue
                # Стаб типизирует элементы кортежа независимо, поэтому
                # проверка `method` не сужает `body`/`delivery_tag`.
                assert body is not None
                assert method.delivery_tag is not None
                exc: BaseException | None = None
                # Исключение ловится снаружи спана: он успевает записать его и
                # закрыться со статусом ошибки, а не OK при nack в DLQ.
                try:
                    with consuming_span(properties, method.delivery_tag, method.redelivered):
                        handler(body)
                except Exception as caught:  # noqa: BLE001 — любая ошибка уводит сообщение в DLQ, а не роняет процесс
                    exc = caught
                if should_ack(exc):
                    self.channel.basic_ack(method.delivery_tag)
                else:
                    self.channel.basic_nack(method.delivery_tag, requeue=False)
                processed += 1
                if limit is not None and processed >= limit:
                    break
                if stop is not None and stop.requested:
                    break
        finally:
            self.channel.cancel()
            self.connection.close()


def build_worker(settings: Settings) -> Worker:
    parameters = pika.URLParameters(settings.rabbitmq_url)
    # Обработчик вызывается синхронно и ходит минутами (запрос к модели), а
    # пока он работает, цикл событий `BlockingConnection` не крутится и
    # heartbeat не отправляется: брокер по умолчанию рвёт такое соединение, и
    # `basic_ack` падает `StreamLostError`. Без heartbeat обрыв не случается.
    parameters.heartbeat = 0
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    declare_topology(channel)
    channel.basic_qos(prefetch_count=1)
    return Worker(connection=connection, channel=channel)


class ReviewHandlerDeps(Protocol):
    """Только то, что нужно обработчику — не весь `Container`."""

    def unit_of_work(self) -> UnitOfWork: ...

    def llm_gateway(self) -> LlmGateway: ...

    def vcs_gateway(self) -> VcsGateway: ...

    def definition_extractor(self) -> DefinitionExtractor: ...

    def cache_store(self) -> CacheStore: ...


def context_limits(settings: Settings) -> ContextLimits:
    """Лимиты сборки контекста из настроек: слой приложения окружения не читает."""
    return ContextLimits(
        max_tokens=settings.context_max_tokens,
        rules_max_tokens=settings.rules_max_tokens,
        rules_max_bytes=settings.rules_max_bytes,
        rules_cache_ttl_seconds=settings.rules_cache_ttl_seconds,
        ast_max_files=settings.ast_max_files,
        ast_max_file_bytes=settings.ast_max_file_bytes,
        pr_context_max_tokens=settings.pr_context_max_tokens,
        diff_max_bytes=settings.diff_max_bytes,
    )


def llm_limits(settings: Settings) -> LlmLimits:
    """Окно модели и резерв под ответ из настроек."""
    return LlmLimits(
        window_tokens=settings.llm_context_window_tokens,
        max_output_tokens=settings.llm_max_output_tokens,
    )


def build_review_handler(
    deps: ReviewHandlerDeps,
    limits: ContextLimits,
    model_limits: LlmLimits,
    prompt_texts: PromptTexts,
) -> MessageHandler:
    stage_tracer = OtelStageTracer()

    def handle(body: bytes) -> None:
        job = from_wire_message(body)
        trace.get_current_span().set_attribute(REVIEW_RUN_ID, str(job.review_run_id))
        try:
            run_review(
                job,
                uow=deps.unit_of_work(),
                llm_gateway=deps.llm_gateway(),
                vcs_gateway=deps.vcs_gateway(),
                definition_extractor=deps.definition_extractor(),
                cache_store=deps.cache_store(),
                context_limits=limits,
                llm_limits=model_limits,
                prompt_texts=prompt_texts,
                now=lambda: datetime.now(UTC),
                monotonic=time.monotonic,
                new_id=new_id,
                stage_tracer=stage_tracer,
            )
        except Exception:
            # Сообщение уйдёт в DLQ; по job_id видно, какая это постановка
            # прогона, по review_run_id — какой прогон.
            logger.exception(
                "задача %s (прогон %s) завершилась ошибкой", job.id, job.review_run_id
            )
            raise

    return handle


class WorkerConfigError(RuntimeError):
    """Конфигурация, с которой воркер заведомо не сможет работать."""


def load_checked_prompt_texts(settings: Settings, root: Path) -> PromptTexts:
    """Тексты промптов и проверка, что полный пакет с ними помещается в окно модели.

    Без текстов воркер не поднимается, а не отправляет модели пустые
    инструкции; с окном, в которое не помещается пакет на пределе лимита, -
    тоже, а не роняет каждый крупный прогон (change `add-llm-gateway`, D5).
    """
    texts = load_prompt_texts(root)
    fits = check_window_config(
        texts,
        context_max_tokens=settings.context_max_tokens,
        max_output_tokens=settings.llm_max_output_tokens,
        window_tokens=settings.llm_context_window_tokens,
    )
    if not fits.ok:
        raise WorkerConfigError(fits.error)
    return texts


def check_model_deadline(settings: Settings) -> None:
    """Один вызов модели в худшем случае короче порога зависшего прогона.

    Иначе выметатель объявил бы мёртвым живой прогон, пока модель ещё
    отвечает. Считается один вызов, а не два: между вызовом ревью и
    верификатором прогон отмечает прогресс (change `llm-gateway-review-fixes`, D4).
    """
    worst = settings.llm_call_worst_case_seconds
    if worst >= settings.stale_run_timeout_seconds:
        raise WorkerConfigError(
            f"вызов модели в худшем случае {worst:g} с (LLM_TIMEOUT_SECONDS × LLM_MAX_ATTEMPTS"
            f" × провайдеры) не короче порога зависшего прогона"
            f" {settings.stale_run_timeout_seconds} с (STALE_RUN_TIMEOUT_SECONDS)"
        )


def check_sweep_thresholds(settings: Settings) -> None:
    """Порог ждущего в очереди прогона больше порога зависшего (change `queued-run-timeout`).

    Иначе прогон, ещё не взятый воркером, выметался бы раньше зависшего в
    работе, а его сообщение воркер потом молча пропустил бы.
    """
    queued, stale = settings.queued_run_timeout_seconds, settings.stale_run_timeout_seconds
    if queued <= stale:
        raise WorkerConfigError(
            f"порог ждущего в очереди прогона {queued} с (QUEUED_RUN_TIMEOUT_SECONDS)"
            f" не больше порога зависшего прогона {stale} с (STALE_RUN_TIMEOUT_SECONDS)"
        )


def build_redelivery(
    settings: Settings,
    deliveries: Callable[[], WebhookDeliveries],
    vcs: Callable[[], VcsGateway],
    *,
    now: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
) -> Callable[[], int] | None:
    """Шаг повтора упавших доставок вебхука (#149) или `None`, если он выключен.

    Без идентификатора или ключа GitHub App JWT не подписать — шаг не собирается,
    и об этом одна строка INFO при старте, а не ошибка на каждом проходе.
    """
    if not settings.webhook_redelivery_enabled:
        logger.info("повтор упавших доставок вебхука выключен: WEBHOOK_REDELIVERY_ENABLED=false")
        return None
    if not settings.github_app_configured:
        logger.info(
            "повтор упавших доставок вебхука выключен: не заданы GITHUB_APP_ID или GITHUB_APP_PRIVATE_KEY"
        )
        return None
    hooks, gateway = deliveries(), vcs()
    policy = RedeliveryPolicy(
        max_attempts=settings.webhook_redelivery_max_attempts,
        backoff=timedelta(seconds=settings.webhook_redelivery_backoff_seconds),
        limit=settings.webhook_redelivery_max_per_pass,
    )

    # `guid` устаревших доставок — в памяти процесса: после рестарта воркер
    # проверит их по разу заново, это дешевле таблицы.
    known_stale: set[str] = set()

    def redeliver() -> int:
        return redeliver_failed_webhooks(
            hooks,
            gateway,
            now=now(),
            policy=policy,
            monotonic=monotonic,
            budget_seconds=settings.webhook_redelivery_pass_budget_seconds,
            known_stale=known_stale,
        )

    return redeliver


def build_maintenance(
    uow_factory: Callable[[], UnitOfWork],
    *,
    stale_limit: timedelta,
    queued_limit: timedelta,
    now: Callable[[], datetime],
    redeliver: Callable[[], int] | None = None,
    redelivery_interval: timedelta = timedelta(seconds=300),
) -> Callable[[], None]:
    """Периодический проход: зависшие прогоны, истёкшие сессии, повтор доставок.

    Шаги независимы: сбой одного логируется, отмечается ошибкой на текущем спане
    (`review.sweep`) и не мешает другому. Повтор доставок ходит в GitHub, поэтому
    идёт реже прохода — не чаще `redelivery_interval`; время его попытки
    запоминается и при сбое, чтобы отказ лимита не повторялся каждую минуту.
    """
    last_redelivery: datetime | None = None

    def maintenance() -> None:
        nonlocal last_redelivery
        try:
            swept = sweep_stale_runs(
                uow_factory(), now=now(), limit=stale_limit, queued_limit=queued_limit
            )
            if swept:
                logger.warning("зависших прогонов переведено в failed: %d", swept)
        except Exception as exc:
            record_current_exception(exc)
            logger.exception("проход по зависшим прогонам завершился ошибкой")
        try:
            purged = purge_expired_sessions(uow_factory(), now=now())
            if purged:
                logger.info("истёкших сессий удалено: %d", purged)
        except Exception as exc:
            record_current_exception(exc)
            logger.exception("удаление истёкших сессий завершилось ошибкой")
        moment = now()
        if redeliver is None or (
            last_redelivery is not None and moment - last_redelivery < redelivery_interval
        ):
            return
        last_redelivery = moment
        try:
            redelivered = redeliver()
            if redelivered:
                logger.info("упавших доставок вебхука переотправлено: %d", redelivered)
        except Exception as exc:
            record_current_exception(exc)
            logger.exception("повтор упавших доставок вебхука завершился ошибкой")

    return maintenance


def run_worker(settings: Settings) -> None:
    """Собрать зависимости и запустить воркер бесконечно. Вызывается из `__main__.py`."""
    # До подключения к брокеру: с непригодной конфигурацией воркер не поднимается.
    # Заглушка модели выбирается только явно - иначе прогоны молча выходили бы
    # пустыми (change `add-llm-gateway`, требование «Выбор заглушки явный»).
    if settings.llm_backend is None:
        raise WorkerConfigError("LLM_BACKEND не задан: выберите stub или ollama")
    check_model_deadline(settings)
    check_sweep_thresholds(settings)
    configure_logging(WORKER_SERVICE)
    configure_telemetry(WORKER_SERVICE)
    container = build_container(settings)
    instrument_engine(container.engine)
    prompt_texts = load_checked_prompt_texts(settings, DEFAULT_PROMPTS_DIR)
    # Собрать адаптер модели сразу: порядок провайдеров уходит в лог при старте.
    container.llm_gateway()
    sweep = build_maintenance(
        container.unit_of_work,
        stale_limit=timedelta(seconds=settings.stale_run_timeout_seconds),
        queued_limit=timedelta(seconds=settings.queued_run_timeout_seconds),
        now=lambda: datetime.now(UTC),
        redeliver=build_redelivery(
            settings,
            container.webhook_deliveries,
            container.vcs_gateway,
            now=lambda: datetime.now(UTC),
        ),
        redelivery_interval=timedelta(seconds=settings.webhook_redelivery_interval_seconds),
    )
    handler = build_review_handler(
        container, context_limits(settings), llm_limits(settings), prompt_texts
    )
    with stop_on_signals() as stop:
        try:
            build_worker(settings).run(handler, sweep=sweep, stop=stop)
        finally:
            shutdown_tracing()
