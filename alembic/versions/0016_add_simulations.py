"""симуляции песочницы правил

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-01 12:00:00.000000

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

_STATUS = postgresql.ENUM(
    'queued', 'building_context', 'analysing', 'verifying', 'completed', 'failed',
    name='simulation_status',
)
_SOURCE_KIND = postgresql.ENUM('code', 'diff', name='simulation_source_kind')
_TERMINAL = "'completed', 'failed'"


def upgrade() -> None:
    """Upgrade схемы: новая таблица, горячие таблицы не трогает."""
    _STATUS.create(op.get_bind())
    _SOURCE_KIND.create(op.get_bind())
    op.create_table('simulations',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('status', postgresql.ENUM(name='simulation_status', create_type=False), nullable=False),
    sa.Column(
        'source_kind', postgresql.ENUM(name='simulation_source_kind', create_type=False), nullable=False
    ),
    sa.Column('file_path', sa.Text(), nullable=True),
    sa.Column('source', sa.Text(), nullable=True),
    sa.Column('rules', sa.Text(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('model', sa.String(length=255), nullable=True),
    sa.Column('tokens_used', sa.BigInteger(), nullable=True),
    # Тип итога второго прохода - общий с прогонами ревью (миграция 0006).
    sa.Column(
        'verification', postgresql.ENUM(name='review_verification', create_type=False), nullable=True
    ),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(source_kind = 'code') = (file_path IS NOT NULL)", name='ck_simulations_file_path'),
    # Вход пользователя - ровно у незавершённой: итог его стирает (design D10).
    sa.CheckConstraint(f'(status IN ({_TERMINAL})) = (source IS NULL)', name='ck_simulations_source'),
    sa.CheckConstraint(f'(status IN ({_TERMINAL})) = (rules IS NULL)', name='ck_simulations_rules'),
    sa.CheckConstraint("(status = 'completed') = (result IS NOT NULL)", name='ck_simulations_result'),
    sa.CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name='ck_simulations_error_code'),
    sa.CheckConstraint('tokens_used IS NULL OR tokens_used >= 0', name='ck_simulations_tokens_non_negative'),
    # Симуляция без пользователя ничего не значит - CASCADE, как у сессий.
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'uq_simulations_one_active_per_user',
        'simulations',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text(f'status NOT IN ({_TERMINAL})'),
    )
    op.create_index('ix_simulations_user_created_at', 'simulations', ['user_id', 'created_at'], unique=False)
    op.create_index('ix_simulations_expires_at', 'simulations', ['expires_at'], unique=False)


def downgrade() -> None:
    """Downgrade схемы: симуляции временные, их потеря ожидаема."""
    op.drop_index('ix_simulations_expires_at', table_name='simulations')
    op.drop_index('ix_simulations_user_created_at', table_name='simulations')
    op.drop_index(
        'uq_simulations_one_active_per_user',
        table_name='simulations',
        postgresql_where=sa.text(f'status NOT IN ({_TERMINAL})'),
    )
    op.drop_table('simulations')
    # Autogenerate типы не удаляет: без этого следующий upgrade упадёт на
    # создании существующих типов.
    _SOURCE_KIND.drop(op.get_bind())
    _STATUS.drop(op.get_bind())
