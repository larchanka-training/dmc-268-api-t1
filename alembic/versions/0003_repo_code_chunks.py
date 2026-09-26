"""профиль репозитория: pgvector и чанки кода

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Должно совпадать с колонкой в модели: размерность задаёт конфигурация,
# и пока она не в коде миграции, модели и схема разъезжаются молча.
EMBEDDING_DIM = 768


def upgrade() -> None:
    """Upgrade схемы."""
    # Расширение не входит в схему приложений, но без него не поднимется
    # столбец vector. IF NOT EXISTS: окружение могло создать его раньше.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        'repo_code_chunks',
        sa.Column('repository_id', sa.Uuid(), nullable=False),
        sa.Column('review_run_id', sa.Uuid(), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('start_line', sa.Integer(), nullable=False),
        sa.Column('end_line', sa.Integer(), nullable=False),
        sa.Column('commit_sha', sa.String(length=64), nullable=False),
        sa.Column('content_sha256', sa.String(length=64), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(EMBEDDING_DIM), nullable=False),
        sa.Column('embedding_model', sa.String(length=255), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['review_run_id'], ['review_runs.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('repository_id', 'content_sha256', name='uq_repo_code_chunks_digest'),
    )
    op.create_index(op.f('ix_repo_code_chunks_repository_id'), 'repo_code_chunks', ['repository_id'], unique=False)
    op.create_index(op.f('ix_repo_code_chunks_review_run_id'), 'repo_code_chunks', ['review_run_id'], unique=False)


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_index(op.f('ix_repo_code_chunks_review_run_id'), table_name='repo_code_chunks')
    op.drop_index(op.f('ix_repo_code_chunks_repository_id'), table_name='repo_code_chunks')
    op.drop_table('repo_code_chunks')
    # Данные профиля не нужны для корректности ревью, поэтому расширение
    # уносим тоже: upgrade в чистом окружении должен работать без ручных шагов.
    op.execute("DROP EXTENSION IF EXISTS vector")
