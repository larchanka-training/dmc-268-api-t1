"""repositories: настройки ревью — фильтр веток, порог merge gate, умолчание авторевью

Change `repository-settings`, design D1–D3. Настройки — колонки самой записи репозитория:
`branch_filter` (глоб-паттерны веток, пусто — все ветки) и `min_severity` (порог merge gate,
тип важности находок `finding_severity`); `auto_review_enabled` получает умолчание `true`.

Умолчания — в базе, а не в коде: деплой применяет миграцию до выката нового API, и в это окно
старый API вставляет записи без новых колонок — они получают настройки контракта (`true`,
`{}`, `critical`). Существующие записи получают те же `{}` и `critical`, `auto_review_enabled`
не меняется: оба пути подключения уже пишут `true`.

`ADD COLUMN … NOT NULL DEFAULT <константа>` и `SET DEFAULT` в PostgreSQL ≥ 11 меняют только
каталог, без перезаписи таблицы: блокировка короткая и укладывается в `lock_timeout`.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-31 10:42:44.656031

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0016'
down_revision: str | Sequence[str] | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    op.add_column(
        'repositories',
        sa.Column('branch_filter', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
    )
    # Тип уже есть — его создала базовая ревизия для находок; `create_type=False` не даёт
    # `add_column` создать его второй раз.
    op.add_column(
        'repositories',
        sa.Column(
            'min_severity',
            postgresql.ENUM(name='finding_severity', create_type=False),
            server_default='critical',
            nullable=False,
        ),
    )
    op.alter_column(
        'repositories',
        'auto_review_enabled',
        existing_type=sa.Boolean(),
        server_default=sa.true(),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade схемы: сохранённые фильтр и порог теряются; тип `finding_severity` остаётся — он у находок."""
    op.alter_column(
        'repositories',
        'auto_review_enabled',
        existing_type=sa.Boolean(),
        server_default=None,
        existing_nullable=False,
    )
    op.drop_column('repositories', 'min_severity')
    op.drop_column('repositories', 'branch_filter')
