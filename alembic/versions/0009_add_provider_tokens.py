"""токен пользователя у провайдера при сессии входа

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-23 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0009'
down_revision: str | Sequence[str] | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    # Только шифротекст (AES-256-GCM). Сессии, открытые до этой ревизии,
    # строки не получают: ручки репозиториев попросят их войти заново
    # (change `repositories-via-user-token`, design D5).
    op.create_table('provider_tokens',
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('access_token_encrypted', sa.LargeBinary(), nullable=False),
    sa.Column('access_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('refresh_token_encrypted', sa.LargeBinary(), nullable=True),
    sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        'refresh_expires_at IS NULL OR refresh_token_encrypted IS NOT NULL',
        name='ck_provider_tokens_refresh_expiry_needs_token',
    ),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('session_id')
    )


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_table('provider_tokens')
