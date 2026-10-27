"""Обвязка Alembic.

Строка подключения приходит из Settings, а не из alembic.ini, чтобы
приложение и миграции не разошлись в том, о какой базе речь.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from app.config import load_settings
from app.infrastructure.db import models  # noqa: F401  (регистрирует таблицы)
from app.infrastructure.db.base import Base
from app.infrastructure.logging import configure_logging
from app.infrastructure.telemetry import (
    MIGRATE_SERVICE,
    configure_telemetry,
    instrument_engine,
    migrate_span,
    shutdown_tracing,
)

config = context.config

# Логи миграций — тем же JSON, что API и воркер. `fileConfig` ниже снимает все
# root-хендлеры (стандартное поведение logging.config), поэтому после него
# вызов повторяется: на root не остаётся никого, вернуть может только наш
# JSON-хендлер. `disable_existing_loggers=False` — чтобы логгеры, созданные
# раньше fileConfig (внутри процесса тестов это логгеры приложения), не
# гасились: проверки «в логе нет ключа» не проходили бы впустую.
configure_logging(MIGRATE_SERVICE)
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
    configure_logging(MIGRATE_SERVICE)

target_metadata = Base.metadata

# Тайм-ауты сессии миграций — только онлайн: offline-SQL уходит на ревью или
# DBA, и решать за него, сколько ждать, незачем. Деплой мигрирует живую базу,
# пока работают старые API и воркер (.github/scripts/deploy/migrate.sh), а
# ALTER TABLE, который ждёт блокировку горячей таблицы, ставит за собой в
# очередь все её чтения — API висит, пока держатель не отпустит. 5 с ожидания
# блокировки — и миграция падает, транзакция откатывается, стенд работает на
# прежней схеме; повторный деплой попробует снова. statement_timeout — потолок
# одного запроса: данные стенда мелкие, любой DDL и перенос данных идут
# секунды, а 2 мин на запрос оставляют шагу миграций (timeout-minutes: 10 в
# deploy.yml) время упасть самому, с логом и откатом, а не быть убитым
# раннером. Обоснование — openspec/changes/archive/*-deploy-migrations-safety.
LOCK_TIMEOUT = "5s"
STATEMENT_TIMEOUT = "2min"


def _dsn() -> str:
    """База, которую мигрируем.

    Вызывающий, который уже выставил опцию, выигрывает — так набор тестов
    направляет миграции на свою базу, не трогая окружение, которое читает
    сервис. Всё остальное идёт через Settings.
    """
    configured = config.get_main_option("sqlalchemy.url", None)
    return configured or load_settings().database_url


# `configparser` считает `%` интерполяцией и отвергает значение как есть,
# поэтому пароль с процент-кодированным символом (`p%40ss` вместо `p@ss` —
# обычный способ положить `@` в DSN) ронял здесь ValueError ещё до всякого
# подключения. Удвоение — единственная точка, где значение попадает в ini, а
# при чтении оно схлопывается обратно, поэтому online и offline режимы видят
# настоящий DSN.
config.set_main_option("sqlalchemy.url", _dsn().replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Телеметрия процесса миграций: провайдер с service.name = dmc-268-migrate
    # ставится до движка и спана. Без `OTEL_TRACES_EXPORTER` экспортёров нет —
    # миграции проходят как раньше.
    configure_telemetry(MIGRATE_SERVICE)
    try:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        # Спаны на запросы к базе: миграционные операторы ложатся в трейс детьми.
        instrument_engine(connectable)
        with (
            migrate_span(),
            connectable.connect() as connection,
        ):
            # SET без LOCAL — на сессию: переживает commit и autocommit_block
            # миграций. Commit закрывает автоначатую транзакцию: иначе alembic
            # счёл бы её внешней и не зафиксировал бы миграции.
            connection.execute(text(f"SET lock_timeout = '{LOCK_TIMEOUT}'"))
            connection.execute(text(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'"))
            connection.commit()
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        # Накопленные спаны выгружаются до выхода процесса — и при ошибке
        # миграции тоже: упавший прогон деплоя тоже сигнал. Исключение
        # наружу не глотается.
        shutdown_tracing()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
