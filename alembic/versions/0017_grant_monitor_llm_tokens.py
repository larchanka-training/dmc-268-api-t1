"""dmc268_monitor читает расход токенов прогонов для алерта бюджета модели

Алерт бюджета (#100, infra/observability/alerts.yml) считает токены с начала
месяца запросом receiver-а sqlquery коллектора под ролью мониторинга. Роль
получает SELECT ровно на две колонки review_runs — created_at и tokens_used:
остальные данные прогонов (репозиторий, PR, вердикт, ошибки) ей недоступны.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-01 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0017'
down_revision: str | Sequence[str] | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Имя роли — как в ревизии 0013 и в конфиге коллектора.
_MONITOR_ROLE = "dmc268_monitor"


def upgrade() -> None:
    """SELECT на created_at и tokens_used прогонов для роли мониторинга."""
    # USAGE на схему — явно, а не через PUBLIC: в схеме, созданной заново
    # (CREATE SCHEMA — так пересобирает базу фикстура тестов), PUBLIC его не
    # получает, и роль не видела бы таблицу вовсе. Схема — текущая, та, где
    # миграции создают таблицы.
    op.execute(
        f"DO $$ BEGIN"
        f" EXECUTE format('GRANT USAGE ON SCHEMA %I TO {_MONITOR_ROLE}', current_schema());"
        f" END $$"
    )
    op.execute(f"GRANT SELECT (created_at, tokens_used) ON review_runs TO {_MONITOR_ROLE}")


def downgrade() -> None:
    """Отозвать права: иначе роль нельзя удалить откатом 0013.

    Роль кластерная, её могли снять до отката (тест ревизии 0013 так и делает):
    REVOKE у несуществующей роли — ошибка, поэтому — только при её наличии.
    """
    op.execute(
        f"DO $$ BEGIN"
        f" IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{_MONITOR_ROLE}')"
        f" THEN REVOKE SELECT (created_at, tokens_used) ON review_runs FROM {_MONITOR_ROLE};"
        f" EXECUTE format('REVOKE USAGE ON SCHEMA %I FROM {_MONITOR_ROLE}', current_schema());"
        f" END IF;"
        f" END $$"
    )
