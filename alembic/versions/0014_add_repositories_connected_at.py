"""repositories.connected_at: мягкое выключение ревью

Когда ревью включено последний раз; `NULL` — выключено (change
`repositories-one-list`, design D4). Запись выключенного остаётся: на неё
ссылаются запросы на изменение, история прогонов не теряется. До ревизии
выключения не было, и включённой считается каждая запись — с момента
регистрации.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-30 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0014'
down_revision: str | Sequence[str] | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    # Умолчание `now()`: в окне деплоя старый API пишет на новую схему без колонки, и его
    # подключение без умолчания создало бы выключенный репозиторий.
    op.add_column(
        'repositories',
        sa.Column('connected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    )
    # Таблица маленькая — одним UPDATE, без пачек.
    op.execute('UPDATE repositories SET connected_at = created_at')


def downgrade() -> None:
    """Downgrade схемы: выключенные снова включаются — состояние выключения хранить негде."""
    op.drop_column('repositories', 'connected_at')
