"""Собирает consume-цикл воркера и доменный обработчик, который в нём крутится.

`Worker`/`build_worker` не знают о доменном пайплайне: принимают обработчик
сообщением-байтами. `build_review_handler` — сборка настоящего обработчика
(разбор сообщения + `run_review`), а `run_worker` — то, что вызывает
`app/worker/__main__.py`, чтобы не импортировать `app.infrastructure`/
`app.application` напрямую (точка входа обязана оставаться тонкой).
Ack/nack-решение — чистая `should_ack`, здесь только вызовы канала.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pika

from app.application.context_assembly import ContextLimits
from app.application.llm_review import LlmLimits
from app.application.ports import (
    CacheStore,
    DefinitionExtractor,
    UnitOfWork,
    VcsGateway,
)
from app.application.ports.llm_gateway import LlmGateway
from app.application.review_pipeline import run_review
from app.application.session_purge import purge_expired_sessions
from app.application.stale_sweep import sweep_stale_runs
from app.config import Settings
from app.domain.ids import new_id
from app.domain.prompt import PromptTexts, check_window_config
from app.infrastructure.container import build_container
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR, load_prompt_texts
from app.infrastructure.queue.rabbitmq import (
    QUEUE_NAME,
    declare_topology,
    from_wire_message,
)
from app.worker.handling import should_ack

logger = logging.getLogger(__name__)

MessageHandler = Callable[[bytes], None]

# Как часто выметать зависшие прогоны. Проход дёргается на каждом такте
# consume-цикла (простой раз в секунду или очередное сообщение) и сам
# ограничивается этим интервалом.
SWEEP_INTERVAL_SECONDS = 60.0


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
        """
        processed = 0
        last_sweep: float | None = None
        try:
            for method, _properties, body in self.channel.consume(
                QUEUE_NAME, inactivity_timeout=1
            ):
                if sweep is not None and (
                    last_sweep is None or time.monotonic() - last_sweep >= sweep_interval
                ):
                    last_sweep = time.monotonic()
                    try:
                        sweep()
                    except Exception:
                        logger.exception("проход по зависшим прогонам завершился ошибкой")
                if method is None:
                    continue
                # Стаб типизирует элементы кортежа независимо, поэтому
                # проверка `method` не сужает `body`/`delivery_tag`.
                assert body is not None
                assert method.delivery_tag is not None
                exc: BaseException | None = None
                try:
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
    def handle(body: bytes) -> None:
        job = from_wire_message(body)
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


def build_maintenance(
    uow_factory: Callable[[], UnitOfWork],
    *,
    stale_limit: timedelta,
    now: Callable[[], datetime],
) -> Callable[[], None]:
    """Периодический проход: зависшие прогоны и истёкшие сессии.

    Шаги независимы: сбой одного логируется и не мешает другому.
    """

    def maintenance() -> None:
        try:
            swept = sweep_stale_runs(uow_factory(), now=now(), limit=stale_limit)
            if swept:
                logger.warning("зависших прогонов переведено в failed: %d", swept)
        except Exception:
            logger.exception("проход по зависшим прогонам завершился ошибкой")
        try:
            purged = purge_expired_sessions(uow_factory(), now=now())
            if purged:
                logger.info("истёкших сессий удалено: %d", purged)
        except Exception:
            logger.exception("удаление истёкших сессий завершилось ошибкой")

    return maintenance


def run_worker(settings: Settings) -> None:
    """Собрать зависимости и запустить воркер бесконечно. Вызывается из `__main__.py`."""
    # До подключения к брокеру: с непригодной конфигурацией воркер не поднимается.
    # Заглушка модели выбирается только явно - иначе прогоны молча выходили бы
    # пустыми (change `add-llm-gateway`, требование «Выбор заглушки явный»).
    if settings.llm_backend is None:
        raise WorkerConfigError("LLM_BACKEND не задан: выберите stub или ollama")
    check_model_deadline(settings)
    container = build_container(settings)
    prompt_texts = load_checked_prompt_texts(settings, DEFAULT_PROMPTS_DIR)
    # Собрать адаптер модели сразу: порядок провайдеров уходит в лог при старте.
    container.llm_gateway()
    sweep = build_maintenance(
        container.unit_of_work,
        stale_limit=timedelta(seconds=settings.stale_run_timeout_seconds),
        now=lambda: datetime.now(UTC),
    )
    handler = build_review_handler(
        container, context_limits(settings), llm_limits(settings), prompt_texts
    )
    build_worker(settings).run(handler, sweep=sweep)
