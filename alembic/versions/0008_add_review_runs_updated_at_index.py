"""индекс review_runs по (updated_at, id) для общего потока событий

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-23 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0008'
down_revision: str | Sequence[str] | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    op.create_index('ix_review_runs_updated_at_id', 'review_runs', ['updated_at', 'id'], unique=False)


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_index('ix_review_runs_updated_at_id', table_name='review_runs')
