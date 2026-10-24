"""Общие фикстуры.

Интеграционным тестам нужен живой PostgreSQL. Они помечены и пропускаются,
когда TEST_DATABASE_URL не задан, поэтому обычный `pytest` остаётся зелёным и
на машине без базы. Так задумано: всё, что ниже адаптеров, — чистое.

Переменная намеренно называется не DATABASE_URL, и отката к ней нет.
`migrated` удаляет схему, а DATABASE_URL — то, что README велит
экспортировать для uvicorn и alembic. Одна переменная на двоих означала бы,
что обычная рабочая консоль в одном `pytest` от пустой базы разработки.
"""

import os
import sys
import threading
from collections.abc import Iterator

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.db import models  # noqa: F401  (регистрирует таблицы)
from app.infrastructure.db.base import Base

# Провайдеры глобальные и ставятся один раз на процесс — до первого
# `create_app`: тогда `configure_telemetry` их не подменяет, и телеметрия всех
# тестов ложится в память (фикстуры `spans` и `metric_reader`), а не уходит наружу.
SPAN_EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(SPAN_EXPORTER))
trace.set_tracer_provider(_provider)

METRIC_READER = InMemoryMetricReader()
metrics.set_meter_provider(MeterProvider(metric_readers=[METRIC_READER]))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_RABBITMQ_URL = os.environ.get("TEST_RABBITMQ_URL")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="needs a live PostgreSQL; set TEST_DATABASE_URL",
)

requires_broker = pytest.mark.skipif(
    not TEST_RABBITMQ_URL,
    reason="needs a live RabbitMQ; set TEST_RABBITMQ_URL",
)

# Живые тесты ходят в настоящий EUrouter с ключом организации (в CI - секрет
# `AI_DMC268_T1`, workflow `llm-live.yml`). Основной прогон их исключает
# маркером, поэтому пропуск без ключа не роняет правило «пропуск - провал».
requires_eurorouter = pytest.mark.skipif(
    not os.environ.get("EUROROUTER_API_KEYS"),
    reason="needs the real EUrouter; set EUROROUTER_API_KEYS, EUROROUTER_BASE_URL, EUROROUTER_MODEL",
)


@pytest.fixture(scope="session")
def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("needs a live PostgreSQL; set TEST_DATABASE_URL")
    return create_engine(TEST_DATABASE_URL, future=True)


@pytest.fixture(scope="session")
def migrated(engine):
    """База на head, пересобирается один раз за сессию."""
    from alembic.config import Config

    from alembic import command

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    # `engine` (собственная зависимость этой фикстуры) уже пропускает сессию
    # тестов, когда переменная не задана, так что к этой строке она задана.
    assert TEST_DATABASE_URL is not None

    config = Config("alembic.ini")
    # Явно направляем alembic на тестовую базу. env.py уважает опцию, которую
    # уже выставил вызывающий, поэтому миграция не уйдёт в ту базу, что
    # окажется в DATABASE_URL.
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.upgrade(config, "head")
    return engine


@pytest.fixture
def clean_db(migrated):
    """Каждый тест стартует с пустой базы.

    Сессионная фикстура откатом изолирует тест от его собственных записей, но
    не от того, что закоммитил другой тест. Очистка таблиц в начале и делает
    набор независимым от порядка.
    """
    tables = ", ".join(sorted(Base.metadata.tables))
    with migrated.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    return migrated


@pytest.fixture
def stack(clean_db):
    """Сквозной стенд `tests/e2e/stack.py` на чистой базе и пустых очередях.

    Общий для E2E, eval и нагрузочного прогона: отличаются модель и объём.
    """
    if not TEST_RABBITMQ_URL:
        pytest.skip("needs a live RabbitMQ; set TEST_RABBITMQ_URL")
    from .e2e.stack import Stack

    built = Stack.build(clean_db, TEST_RABBITMQ_URL)
    try:
        yield built
    finally:
        built.purge_queues()


@pytest.fixture
def session(clean_db) -> Iterator[Session]:
    """Сессия, откатывается после каждого теста, чтобы тесты не видели друг друга."""
    connection = clean_db.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        # Тест на ограничение базы роняет транзакцию сам: после IntegrityError
        # она уже отвязана от соединения, и повторный откат только предупреждает.
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _forget_spans() -> Iterator[None]:
    """Спаны теста не копятся в памяти до конца сессии: экспортёр общий на все тесты."""
    yield
    SPAN_EXPORTER.clear()


@pytest.fixture(autouse=True)
def _restore_excepthooks() -> Iterator[None]:
    """`run_worker` ставит глобальные excepthook'и — тест возвращает прежние.

    `run_worker` вызывается не только тестами старта воркера, но и тестами
    цикла и телеметрии, поэтому фикстура общая на все тесты: без неё хуки
    утекали бы на всю pytest-сессию.
    """
    sys_hook, thread_hook = sys.excepthook, threading.excepthook
    yield
    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


@pytest.fixture(scope="session")
def metric_reader() -> InMemoryMetricReader:
    """Метрики бизнес-сигналов — из глобального провайдера в память.

    Значения приборов копятся кумулятивно за сессию, поэтому тесты сверяют
    приращение между двумя сборами, а не абсолют.
    """
    return METRIC_READER


@pytest.fixture
def spans() -> InMemorySpanExporter:
    """Спаны, законченные за этот тест.

    Очистка и здесь, а не только после теста: фикстуры, поднятые раньше `spans`
    (`clean_db` на инструментированном движке), уже могли закончить свои спаны.
    """
    SPAN_EXPORTER.clear()
    return SPAN_EXPORTER
