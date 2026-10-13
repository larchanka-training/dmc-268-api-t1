"""этап сбоя у прогона ревью

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    # Этап, на котором прогон упал. Nullable: у идущих и успешных прогонов
    # этапа сбоя нет, а упавшим до появления колонки проставить его нечем —
    # прежний статус не сохранялся. Тип — существующий `review_run_status`,
    # поэтому `create_type=False`: создавать его второй раз нельзя.
    op.add_column(
        "review_runs",
        sa.Column(
            "failed_stage",
            postgresql.ENUM(name="review_run_status", create_type=False),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_review_runs_failed_stage",
        "review_runs",
        "failed_stage IS NULL"
        " OR (status = 'failed' AND failed_stage IN ('analysing', 'building_context', 'publishing'))",
    )


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_constraint("ck_review_runs_failed_stage", "review_runs", type_="check")
    # Тип `review_run_status` остаётся: им пользуется колонка `status`.
    op.drop_column("review_runs", "failed_stage")
