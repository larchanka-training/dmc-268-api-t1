"""base_sha у прогона ревью

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-23 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0002'
down_revision: str | Sequence[str] | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    # Ревизия, относительно которой считался дифф прогона. Nullable: прогоны,
    # записанные до появления колонки, базу сравнения не знают, а проставить
    # её задним числом нельзя — запрос на изменения с тех пор ушёл вперёд.
    op.add_column("review_runs", sa.Column("base_sha", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_column("review_runs", "base_sha")
