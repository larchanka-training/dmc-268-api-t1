"""Собирает consume-цикл воркера и доменный обработчик, который в нём крутится.

`Worker`/`build_worker` не знают о доменном пайплайне: принимают обработчик
сообщением-байтами. `build_review_handler` — сборка настоящего обработчика
(разбор сообщения + `run_review`), а `run_worker` — то, что вызывает
`app/worker/__main__.py`, чтобы не импортировать `app.infrastructure`/
`app.application` напрямую (точка входа обязана оставаться тонкой).
Решение по сообщению — чистая `decide` (ACK / RETRY / DEAD_LETTER), здесь только вызовы канала.
"""

import contextlib
import logging
import signal
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Protocol

import pika
from opentelemetry import trace
from pika import spec

from app.application.context_assembly import ContextLimits
from app.application.context_payload_expiry import expire_context_payloads
from app.application.llm_review import LlmLimits
from app.application.ports import (
    CacheStore,
    DefinitionExtractor,
    InstallationCatalog,
    UnitOfWork,
    VcsGateway,
)
from app.application.ports.llm_gateway import LlmGateway
from app.application.ports.run_outcome import NO_RUN_OUTCOME, RunOutcomeRecorder
from app.application.ports.webhook_deliveries import WebhookDeliveries
from app.application.review_pipeline import run_review
from app.application.session_purge import purge_expired_sessions
from app.application.stale_sweep import sweep_stale_runs
from app.application.webhook_redelivery import redeliver_failed_webhooks
from app.config import Settings
from app.domain.ids import new_id
from app.domain.prompt import PromptTexts, check_window_config
from app.domain.webhook_redelivery import RedeliveryPolicy
from app.infrastructure.container import (
    WORKER_VCS_LIMITS,
    build_container,
    build_llm_balance,
)
from app.infrastructure.logging import (
    configure_logging,
    install_excepthooks,
    review_run_id_scope,
)
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR, load_prompt_texts
from app.infrastructure.queue.rabbitmq import (
    CONSUMER_ARGUMENTS,
    CONSUMER_TIMEOUT_SECONDS,
    QUEUE_NAME,
    consuming_span,
    declare_topology,
    from_wire_message,
)
from app.infrastructure.telemetry import (
    REVIEW_RUN_ID,
    WORKER_SERVICE,
    OtelRunOutcomeRecorder,
    OtelStageTracer,
    configure_telemetry,
    instrument_engine,
    instrument_llm_balance,
    instrument_pool,
    prime_run_outcomes,
    record_current_exception,
    shutdown_tracing,
    sweep_span,
)
from app.worker.handling import Decision, decide

logger = logging.getLogger(__name__)

MessageHandler = Callable[[bytes], None]

# Как часто выметать зависшие прогоны. Проход дёргается на каждом такте
# consume-цикла (простой раз в секунду или очередное сообщение) и сам
# ограничивается этим интервалом.
SWEEP_INTERVAL_SECONDS = 60.0

# Heartbeat AMQP воркера (#157): обработчик идёт в своём потоке, а цикл соединения крутится
# и отвечает брокеру, так что мёртвое соединение брокер замечает за два интервала, а живое
# не рвёт и на прогоне в минуты.
WORKER_HEARTBEAT_SECONDS = 30

# Как часто цикл соединения проверяет, закончил ли обработчик или проход очистки: заодно шаг
# ответа на heartbeat.
_HANDLER_POLL_SECONDS = 1

# Пауза перед возвратом не начатого прогона в очередь (#157). Брокер отдаёт возвращённое
# сообщение сразу, и без паузы все `DELIVERY_LIMIT` повторов сгорают за секунды — база,
# моргнувшая на секунду, отправила бы прогон в DLQ. С паузой у сбоя есть около минуты.
# Пауза идёт циклом соединения (`connection.sleep`): heartbeat она не прерывает.
RETRY_DELAY_SECONDS = 15

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
        retry_delay: float = RETRY_DELAY_SECONDS,
    ) -> None:
        """Потреблять очередь задач ревью (`QUEUE_NAME`).

        `limit=None` — бесконечно (боевой запуск `python -m app.worker`);
        конечное число — для тестов, чтобы не держать процесс висящим на
        пустой очереди. В любом случае при выходе отменяем регистрацию
        консьюмера и закрываем соединение — иначе брокер продолжает считать
        канал живым консьюмером и делит с ним следующие сообщения, хотя
        Python-цикл их уже не читает.

        `sweep` — периодический проход по зависшим прогонам; его сбой
        логируется и не останавливает потребление. Он ходит в базу и GitHub, поэтому
        идёт, как обработчик, в своём потоке: цикл соединения отвечает на heartbeat.

        `retry_delay` — пауза перед возвратом не начатого прогона в очередь
        (`RETRY_DELAY_SECONDS`); просьба остановиться её прерывает.

        `stop` — просьба остановиться (`stop_on_signals`). Сообщение в работе
        дорабатывает и подтверждается, следующее не берётся, даже если брокер
        уже выдал его циклу. На простое цикл выходит на ближайшем такте, не
        позже `inactivity_timeout`. Потребитель снимается до того, как выданное
        сообщение отпущено, — почему, сказано у `_stop_consuming`.

        Брокер может сам отменить потребителя (`x-consumer-timeout`): цикл тогда
        кончается, это пишется в лог, и процесс выходит под перезапуск.
        """
        processed = 0
        last_sweep: float | None = None
        consuming = True
        try:
            for method, properties, body in self.channel.consume(
                QUEUE_NAME, inactivity_timeout=1, arguments=CONSUMER_ARGUMENTS
            ):
                if stop is not None and stop.requested:
                    self._stop_consuming(method)
                    consuming = False
                    break
                if sweep is not None and (
                    last_sweep is None or time.monotonic() - last_sweep >= sweep_interval
                ):
                    last_sweep = time.monotonic()
                    self._sweep(sweep)
                    # Сигнал мог прийти посреди прохода — сообщение этого такта не берём.
                    if stop is not None and stop.requested:
                        self._stop_consuming(method)
                        consuming = False
                        break
                if method is None:
                    continue
                # Стаб типизирует элементы кортежа независимо, поэтому
                # проверка `method` не сужает `body`/`delivery_tag`.
                assert body is not None
                assert method.delivery_tag is not None
                exc = self._handle(handler, body, properties, method)
                processed += 1
                last = (limit is not None and processed >= limit) or (
                    stop is not None and stop.requested
                )
                if last:
                    # До ack, как в `_stop_consuming`.
                    self.channel.cancel()
                    consuming = False
                decision = decide(exc)
                if decision is Decision.ACK:
                    self.channel.basic_ack(method.delivery_tag)
                elif decision is Decision.RETRY:
                    # `basic.reject` с requeue засчитывается попыткой доставки: после
                    # `DELIVERY_LIMIT` брокер уведёт сообщение в DLQ, а не крутит его вечно.
                    # `nack(requeue=True)` попытку не тратит — он для штатной остановки.
                    logger.warning("прогон не начат из-за временного сбоя, сообщение вернётся в очередь")
                    self._pause_before_retry(retry_delay, stop)
                    self.channel.basic_reject(method.delivery_tag, requeue=True)
                else:
                    self.channel.basic_nack(method.delivery_tag, requeue=False)
                if last:
                    break
            else:
                # Бесконечный `consume` кончается сам, только если брокер отменил
                # потребителя: `x-consumer-timeout`, удаление очереди, failover.
                consuming = False
                logger.warning(
                    "брокер отменил потребителя очереди %s: воркер выходит, сообщение без "
                    "подтверждения вернётся в очередь",
                    QUEUE_NAME,
                )
        finally:
            if consuming:
                self.channel.cancel()
            self.connection.close()

    def _handle(
        self,
        handler: MessageHandler,
        body: bytes,
        properties: spec.BasicProperties | None,
        method: spec.Basic.Deliver,
    ) -> Exception | None:
        """Обработать сообщение в отдельном потоке, пока главный крутит цикл соединения.

        Спан — внутри потока: контекст трассировки обработчика его. Исключение ловится снаружи
        спана: он успевает записать его и закрыться со статусом ошибки, а не OK при nack в DLQ.
        """
        delivery_tag = method.delivery_tag
        assert delivery_tag is not None

        def work() -> None:
            with consuming_span(properties, delivery_tag, method.redelivered):
                handler(body)

        return self._beside_connection(work, name="review-handler")

    def _sweep(self, sweep: Callable[[], object]) -> None:
        """Проход очистки в отдельном потоке: он ходит в базу и GitHub и может идти дольше двух
        интервалов heartbeat. Сбой логируется и не останавливает потребление."""

        def work() -> None:
            with sweep_span():
                sweep()

        failure = self._beside_connection(work, name="review-sweep")
        if failure is not None:
            logger.error("проход по зависшим прогонам завершился ошибкой", exc_info=failure)

    def _beside_connection(self, work: Callable[[], None], *, name: str) -> Exception | None:
        """Выполнить `work` в отдельном потоке, пока главный крутит цикл соединения.

        `BlockingConnection` отвечает брокеру на heartbeat только внутри своего цикла: при
        работе в главном потоке он стоит минутами, и брокер рвёт соединение. Канал pika не
        потокобезопасен, поэтому `work` к нему не прикасается — ack и reject делает главный
        поток потом. Исключение `work` возвращается; `KeyboardInterrupt` и `SystemExit`
        пробрасываются в главный поток.
        """
        failure: list[BaseException] = []

        def target() -> None:
            try:
                work()
            # BaseException тоже: в потоке он молча убил бы поток, а сообщение ушло бы в ack.
            except BaseException as caught:  # noqa: BLE001 — исход решает вызывающий или главный поток
                failure.append(caught)

        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        while thread.is_alive():
            try:
                self.connection.process_data_events(time_limit=_HANDLER_POLL_SECONDS)
            except Exception:
                # Соединение потеряно посреди работы: она доводится до конца, а не обрывается
                # выходом процесса. Сообщение без подтверждения брокер вернёт, и повтор
                # подтвердит уже завершённый прогон (`run_review` его пропускает).
                thread.join()
                raise
            thread.join(timeout=0)
        if not failure:
            return None
        caught = failure[0]
        if not isinstance(caught, Exception):
            # `KeyboardInterrupt`, `SystemExit` — как без потока: процесс выходит без
            # подтверждения, и брокер вернёт сообщение с тратой попытки доставки.
            raise caught
        return caught

    def _pause_before_retry(self, delay: float, stop: StopRequest | None) -> None:
        """Подождать `delay` секунд циклом соединения — он отвечает на heartbeat. Просьба
        остановиться паузу прерывает: сообщение возвращается сразу."""
        remaining = delay
        while remaining > 0 and not (stop is not None and stop.requested):
            step = min(remaining, _HANDLER_POLL_SECONDS)
            self.connection.sleep(step)
            remaining -= step

    def _stop_consuming(self, method: spec.Basic.Deliver | None) -> None:
        """Штатная остановка: снять потребителя, потом вернуть выданное сообщение `nack`
        с requeue — он попытку доставки не тратит, а `basic.reject` от `cancel()` и
        возврат при закрытии соединения тратят (#223). Сначала `cancel`: пока
        сообщение держит prefetch=1, брокер не пришлёт следующее, которое `cancel()`
        вернул бы `basic.reject`."""
        self.channel.cancel()
        if method is not None:
            assert method.delivery_tag is not None
            self.channel.basic_nack(method.delivery_tag, requeue=True)


def build_worker(settings: Settings, *, heartbeat: int = WORKER_HEARTBEAT_SECONDS) -> Worker:
    parameters = pika.URLParameters(settings.rabbitmq_url)
    # Обработчик ходит минутами (запрос к модели), но в своём потоке (`Worker._handle`),
    # а цикл `BlockingConnection` всё это время отвечает на heartbeat: мёртвое соединение
    # брокер видит, а живое не рвёт (#157).
    parameters.heartbeat = heartbeat
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
        diff_file_max_bytes=settings.diff_file_max_bytes,
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
    *,
    run_outcome: RunOutcomeRecorder = NO_RUN_OUTCOME,
    billing_enabled: bool = False,
) -> MessageHandler:
    """Собрать обработчик сообщения: разбор + `run_review`.

    `billing_enabled` — проверять баланс плательщика перед вызовом модели (`BILLING_ENABLED`).

    `run_outcome` — запись исходов прогонов в бизнес-метрики; в проде его
    передаёт composition root (`run_worker`) адаптером телеметрии, общим для
    обработчика и прохода очистки. Обработчик сам счётчик не пишет: прогон
    считается на фактическом терминальном переходе, который делает пайплайн.
    """
    stage_tracer = OtelStageTracer()

    def handle(body: bytes) -> None:
        job = from_wire_message(body)
        trace.get_current_span().set_attribute(REVIEW_RUN_ID, str(job.review_run_id))
        # Контекст логов на всё сообщение: каждую запись пайплайна фильтр
        # хендлера дополняет атрибутом review_run_id — по полю его ищут Loki
        # и алерты, вклейка в текст не ищется (спека «Логи»).
        with review_run_id_scope(str(job.review_run_id)):
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
                    run_outcome=run_outcome,
                    billing_enabled=billing_enabled,
                )
            except Exception:
                # job.id остаётся в тексте: это идентификатор задачи очереди, у
                # спанов поля с таким именем нет. Прогон ищется по атрибуту
                # review_run_id, который ставит фильтр из контекста выше.
                logger.exception("задача %s завершилась ошибкой", job.id)
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


def check_consumer_timeout(settings: Settings) -> None:
    """Худший прогон короче таймаута подтверждения потребителя (#223).

    Иначе брокер забрал бы сообщение у живого воркера и отдал его следующему
    потребителю, и тот перевёл бы прогон в `failed` из-под первого. Худший прогон —
    как грейс остановки воркера: два вызова модели в худшем случае плюс минута на
    GitHub и базу (`local.worker_stop_seconds` в infra).
    """
    worst = 2 * settings.llm_call_worst_case_seconds + 60
    if worst >= CONSUMER_TIMEOUT_SECONDS:
        raise WorkerConfigError(
            f"прогон в худшем случае {worst:g} с (два вызова модели × LLM_TIMEOUT_SECONDS"
            f" × LLM_MAX_ATTEMPTS × провайдеры + 60) не короче таймаута подтверждения"
            f" потребителя {CONSUMER_TIMEOUT_SECONDS} с (константа CONSUMER_TIMEOUT_SECONDS"
            f" в app/infrastructure/queue/rabbitmq.py)"
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
    run_outcome: RunOutcomeRecorder = NO_RUN_OUTCOME,
    vcs: VcsGateway | None = None,
    installations: InstallationCatalog | None = None,
    payload_retention: timedelta = timedelta(days=30),
    payload_expiry_batch: int = 200,
) -> Callable[[], None]:
    """Периодический проход: зависшие прогоны, истёкшие сессии, тела пакетов контекста старше
    срока хранения, повтор доставок.

    Шаги независимы: сбой одного логируется, отмечается ошибкой на текущем спане
    (`review.sweep`) и не мешает другому. Повтор доставок ходит в GitHub, поэтому
    идёт реже прохода — не чаще `redelivery_interval`; время его попытки
    запоминается и при сбое, чтобы отказ лимита не повторялся каждую минуту.
    Выметенные прогоны — терминальные исходы, их пишет `run_outcome`, как и
    пайплайн свои; gate их head выставляется через `vcs` и `installations`.
    """
    last_redelivery: datetime | None = None

    def maintenance() -> None:
        nonlocal last_redelivery
        try:
            swept = sweep_stale_runs(
                uow_factory(),
                now=now(),
                limit=stale_limit,
                queued_limit=queued_limit,
                run_outcome=run_outcome,
                vcs=vcs,
                installations=installations,
            )
            if swept:
                logger.warning("зависших прогонов переведено в failed: %d", swept)
        except Exception as exc:
            record_current_exception(exc)
            # Тело записи с исключением коллектор гасит: шаг различает только поле.
            logger.exception(
                "проход по зависшим прогонам завершился ошибкой",
                extra={"maintenance_step": "sweep_stale_runs"},
            )
        try:
            purged = purge_expired_sessions(uow_factory(), now=now())
            if purged:
                logger.info("истёкших сессий удалено: %d", purged)
        except Exception as exc:
            record_current_exception(exc)
            logger.exception(
                "удаление истёкших сессий завершилось ошибкой",
                extra={"maintenance_step": "purge_expired_sessions"},
            )
        try:
            expired = expire_context_payloads(
                uow_factory(), now=now(), retention=payload_retention, batch=payload_expiry_batch
            )
            if expired:
                # Счётчик — полем, а не в тексте: по нему ищут Loki и алерты.
                logger.info(
                    "тела пакетов контекста стёрты по сроку хранения",
                    extra={"maintenance_step": "expire_context_payloads", "expired_count": expired},
                )
        except Exception as exc:
            record_current_exception(exc)
            logger.exception(
                "стирание тел пакетов контекста завершилось ошибкой",
                extra={"maintenance_step": "expire_context_payloads"},
            )
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
            logger.exception(
                "повтор упавших доставок вебхука завершился ошибкой",
                extra={"maintenance_step": "redeliver_webhooks"},
            )

    return maintenance


def run_worker(load_settings: Callable[[], Settings]) -> None:
    """Собрать зависимости и запустить воркер бесконечно. Вызывается из `__main__.py`.

    Настройки приходят загрузчиком, а не готовыми: `load_settings` вызывается
    внутри, уже после настройки логов, телеметрии и excepthook'ов. Иначе
    `ValidationError` pydantic из кривого окружения выходила бы до настройки
    логов текстом в stderr, коллектор её дропал бы, и crash-loop воркера был
    бы невидим в Loki (замечание ревью PR #130).
    """
    configure_logging(WORKER_SERVICE)
    # Телеметрия — сразу за логами и до чтения окружения: OTLP-хендлер
    # должен стоять, когда WorkerConfigError пишется, иначе ошибка старта
    # воркера до Loki не доходит (замечание ревью PR #130). Провайдер собирается
    # из env, настройки сервиса ему не нужны.
    configure_telemetry(WORKER_SERVICE)
    # Ряды исходов прогонов — нулём со старта: иначе первое падение после
    # рестарта `increase()` в `ReviewRunsFailing` не видит.
    prime_run_outcomes()
    # Хук общий для API и воркера (живёт в `app.infrastructure.logging`):
    # необработанное исключение любого потока и ошибка старта выходят
    # JSON-записью, а не текстовым дампом в stderr, который коллектор
    # дропает (замечание ревью PR #130).
    install_excepthooks()
    # Окружение читается последним из стартовых шагов: любая ошибка чтения
    # конфигурации — `ValidationError` pydantic или `WorkerConfigError` —
    # выходит из процесса JSON-записью через уже стоящий excepthook
    # (замечание ревью PR #130), и crash-loop воркера виден в Loki.
    settings = load_settings()
    # До подключения к брокеру: с непригодной конфигурацией воркер не поднимается.
    # Заглушка модели выбирается только явно - иначе прогоны молча выходили бы
    # пустыми (change `add-llm-gateway`, требование «Выбор заглушки явный»).
    if settings.llm_backend is None:
        raise WorkerConfigError("LLM_BACKEND не задан: выберите stub, ollama или eurorouter")
    check_model_deadline(settings)
    check_consumer_timeout(settings)
    check_sweep_thresholds(settings)
    # Воркеру — свои потолки VCS-клиента: дифф и ревью крупного PR дольше,
    # чем ответ на вебхук у api.
    container = build_container(settings, vcs_limits=WORKER_VCS_LIMITS)
    instrument_engine(container.engine)
    instrument_pool(container.engine, settings.db_pool_size, settings.db_max_overflow)
    prompt_texts = load_checked_prompt_texts(settings, DEFAULT_PROMPTS_DIR)
    # Собрать адаптер модели сразу: порядок провайдеров уходит в лог при старте.
    container.llm_gateway()
    # Баланс провайдера - там, где код может уйти Eurorouter: основным или фоллбэком.
    if settings.llm_backend == "eurorouter" or settings.llm_hosted_fallback:
        instrument_llm_balance(build_llm_balance(settings))
    # Один адаптер на оба потребителя: запись исходов — общая бизнес-метрика
    # пайплайна и прохода очистки.
    run_outcome = OtelRunOutcomeRecorder()
    sweep = build_maintenance(
        container.unit_of_work,
        stale_limit=timedelta(seconds=settings.stale_run_timeout_seconds),
        queued_limit=timedelta(seconds=settings.queued_run_timeout_seconds),
        now=lambda: datetime.now(UTC),
        redeliver=build_redelivery(
            settings,
            container.webhook_deliveries,
            container.redelivery_vcs_gateway,
            now=lambda: datetime.now(UTC),
        ),
        redelivery_interval=timedelta(seconds=settings.webhook_redelivery_interval_seconds),
        run_outcome=run_outcome,
        # Шлюз без ожидания лимита, как у повтора доставок: проход идёт в consume-цикле.
        vcs=container.redelivery_vcs_gateway(),
        installations=container.installation_catalog(),
        payload_retention=timedelta(days=settings.context_payload_retention_days),
        payload_expiry_batch=settings.context_payload_expiry_batch,
    )
    handler = build_review_handler(
        container,
        context_limits(settings),
        llm_limits(settings),
        prompt_texts,
        run_outcome=run_outcome,
        billing_enabled=settings.billing_enabled,
    )
    with stop_on_signals() as stop:
        try:
            build_worker(settings).run(handler, sweep=sweep, stop=stop)
        finally:
            shutdown_tracing()
