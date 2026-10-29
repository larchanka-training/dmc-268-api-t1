"""роль dmc268_monitor и pg_stat_statements для метрик базы

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-24 12:00:00.000000

"""
import os
from collections.abc import Sequence

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Имя роли зашито: под ним коллектор ходит в базу (receiver postgresql в
# infra/observability/otelcol.yaml) — переименовать можно только вместе с
# конфигом стека в обоих окружениях.
_MONITOR_ROLE = "dmc268_monitor"


def _monitor_password() -> str:
    """Пароль роли — прямо из окружения, без валидации Settings.

    Полная сборка настроек здесь не нужна и вредна: ревизии выполняются в
    разных окружениях (CI, миграционный контейнер, локально), где настроена
    только база, а для роли нужен лишь пароль строкой в DDL — `load_settings`
    требовал бы ещё `RABBITMQ_URL` и `GITHUB_WEBHOOK_SECRET` и ронял бы
    миграцию на свежей базе в CI. Дефолт — локальные реквизиты compose (как
    пара dmc268/dmc268, допустимы только локально); на стенде пароль задаёт
    POSTGRES_MONITOR_PASSWORD из секрета.
    """
    return os.environ.get("POSTGRES_MONITOR_PASSWORD", "dmc268_monitor")


def upgrade() -> None:
    """Роль только-чтение с привилегией pg_monitor и расширение статистики."""
    # Пароль — из окружения, один источник с env коллектора
    # (POSTGRESQL_PASSWORD): иначе роль и подключение разъедутся. Кавычки в
    # литерале SQL удваиваются: пароль задаёт человек.
    password = _monitor_password().replace("'", "''")
    # bind-параметры в utility-выражениях PostgreSQL не поддерживаются
    # (грамматика PASSWORD принимает только литерал, не $1), поэтому пароль
    # попадает в литерал, а не в плейсхолдер.
    # Роль кластерная — её видно всем базам инстанции: на второй базе того
    # же кластера (тестовая, вторая схема) роль уже создана первой. Создаём
    # при отсутствии (в DO-блоке — только зашитое имя, без пароля), пароль
    # и LOGIN выставляем всегда: миграция обязана сводить роль с тем
    # паролем, что задан окружению коллектора.
    op.execute(
        f"DO $$ BEGIN"
        f" IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_MONITOR_ROLE}')"
        f" THEN CREATE ROLE {_MONITOR_ROLE}; END IF;"
        f" END $$"
    )
    # track_utility выключается до ALTER ROLE в той же транзакции (SET LOCAL
    # живёт до конца транзакции, ревизию alembic выполняет в одной): иначе
    # пароль осел бы в pg_stat_statements открытым текстом — утилитные
    # выражения этот трекер пишет как есть, не заменяя константы
    # плейсхолдерами (проверено на PostgreSQL 18). Возврат вручную не нужен:
    # SET LOCAL умирает на COMMIT. Если библиотека не загружена (shared_preload
    # выключен), SET LOCAL спокойно создаёт placeholder-параметр и ничего
    # не ломает. Тот же приём — у перегенерации пароля на деплое
    # (infra/main.tf, docker-compose.yml).
    op.execute("SET LOCAL pg_stat_statements.track_utility = off")
    op.execute(f"ALTER ROLE {_MONITOR_ROLE} WITH LOGIN PASSWORD '{password}'")
    # pg_monitor — предопределённая роль со статистическими вьюхами
    # (pg_stat_activity, pg_stat_database, pg_stat_statements): других
    # привилегий роли мониторинга не нужно, данные она не читает. Повторный
    # GRANT уже выданного членства — notice, не ошибка.
    op.execute(f"GRANT pg_monitor TO {_MONITOR_ROLE}")
    # Расширение остаётся для ручной диагностики медленных запросов: receiver
    # postgresql коллектора его не читает (метрики базы он снимает обычными
    # вьюхами статистики). Библиотека уже загружена через
    # shared_preload_libraries — её включает контейнер базы
    # (docker-compose.yml и infra/main.tf), здесь создаётся расширение.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")


def downgrade() -> None:
    """Удалить расширение и роль: роль ничего не владеет."""
    op.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
    op.execute(f"DROP ROLE IF EXISTS {_MONITOR_ROLE}")
