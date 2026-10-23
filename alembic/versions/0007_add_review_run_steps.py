"""шаги прогона ревью

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0007'
down_revision: str | Sequence[str] | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STAGE = postgresql.ENUM(
    'fetching_diff', 'building_context', 'analysing', 'verifying', 'publishing',
    name='review_run_stage',
)
_STEP_STATUS = postgresql.ENUM('running', 'completed', 'failed', name='review_run_step_status')


def upgrade() -> None:
    """Upgrade схемы."""
    _STAGE.create(op.get_bind())
    _STEP_STATUS.create(op.get_bind())
    op.create_table('review_run_steps',
    sa.Column('review_run_id', sa.Uuid(), nullable=False),
    sa.Column('stage', postgresql.ENUM(name='review_run_stage', create_type=False), nullable=False),
    sa.Column(
        'status', postgresql.ENUM(name='review_run_step_status', create_type=False), nullable=False
    ),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('tokens_used', sa.BigInteger(), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    # Длительности нет ровно у идущего шага, кода сбоя — у всех, кроме упавшего.
    sa.CheckConstraint(
        "(status = 'running') = (duration_seconds IS NULL)",
        name='ck_review_run_steps_duration',
    ),
    sa.CheckConstraint(
        "(status = 'failed') = (error_code IS NOT NULL)",
        name='ck_review_run_steps_error_code',
    ),
    sa.CheckConstraint(
        'duration_seconds IS NULL OR duration_seconds >= 0',
        name='ck_review_run_steps_duration_non_negative',
    ),
    sa.CheckConstraint(
        'tokens_used IS NULL OR tokens_used >= 0',
        name='ck_review_run_steps_tokens_non_negative',
    ),
    # RESTRICT, как у всех детей прогона: история не пропадает побочным эффектом.
    sa.ForeignKeyConstraint(['review_run_id'], ['review_runs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    # Порядок `list_for_run`: шаги прогона читаются отсортированными по индексу.
    op.create_index(
        'ix_review_run_steps_run_order',
        'review_run_steps',
        ['review_run_id', 'started_at', 'id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_index('ix_review_run_steps_run_order', table_name='review_run_steps')
    op.drop_table('review_run_steps')
    # Autogenerate типы не удаляет: без этого следующий upgrade упадёт на
    # создании существующих типов.
    _STEP_STATUS.drop(op.get_bind())
    _STAGE.drop(op.get_bind())
