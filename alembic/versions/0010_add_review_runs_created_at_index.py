"""индекс review_runs по (created_at, id) для списка прогонов

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-23 18:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0010'
down_revision: str | Sequence[str] | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    op.create_index('ix_review_runs_created_at_id', 'review_runs', ['created_at', 'id'], unique=False)


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_index('ix_review_runs_created_at_id', table_name='review_runs')
