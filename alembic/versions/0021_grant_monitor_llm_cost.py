"""dmc268_monitor читает стоимость прогонов для алерта бюджета в евро

Алерт бюджета в евро (#119, infra/observability/alerts.yml) суммирует
`review_runs.cost_eur` с начала месяца тем же запросом receiver-а sqlquery
коллектора, что и токены (ревизия 0017). Роль получает SELECT ещё на одну
колонку — `cost_eur`; остальные данные прогонов ей по-прежнему недоступны.
USAGE на схему уже дала 0017.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-10 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0021'
down_revision: str | Sequence[str] | None = '0020'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Имя роли — как в ревизии 0013 и в конфиге коллектора.
_MONITOR_ROLE = "dmc268_monitor"


def upgrade() -> None:
    """SELECT на cost_eur прогонов для роли мониторинга."""
    op.execute(
        f"DO $$ BEGIN"
        f" EXECUTE format('GRANT SELECT (cost_eur) ON %I.review_runs TO {_MONITOR_ROLE}', current_schema());"
        f" END $$"
    )


def downgrade() -> None:
    """Отозвать право; роли может не быть (см. откат 0017)."""
    op.execute(
        f"DO $$ BEGIN"
        f" IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{_MONITOR_ROLE}')"
        f" THEN EXECUTE format('REVOKE SELECT (cost_eur) ON %I.review_runs FROM {_MONITOR_ROLE}', current_schema());"
        f" END IF;"
        f" END $$"
    )
