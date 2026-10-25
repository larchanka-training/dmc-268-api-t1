"""UNIQUE на published_comments (provider_comment_id, kind)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-24 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0011'
down_revision: str | Sequence[str] | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    op.create_unique_constraint(
        'uq_published_comments_provider_comment_id', 'published_comments', ['provider_comment_id', 'kind']
    )


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_constraint('uq_published_comments_provider_comment_id', 'published_comments', type_='unique')
