"""findings.covered_by_finding_id и published_comments.thread_resolved_by_bot

Повторный прогон не дублирует замечание, у которого уже есть тред бота:
находка ссылается на находку этого треда. Тред, закрытый ботом, отличается
от закрытого человеком: замечание после закрытия человеком не повторяется.
Треды, закрытые до ревизии, считаются закрытыми ботом.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-25 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0012'
down_revision: str | Sequence[str] | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    op.add_column('findings', sa.Column('covered_by_finding_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'findings_covered_by_finding_id_fkey',
        'findings',
        'findings',
        ['covered_by_finding_id'],
        ['id'],
        ondelete='RESTRICT',
    )
    op.create_check_constraint(
        'ck_findings_covered_by_other',
        'findings',
        'covered_by_finding_id IS NULL OR covered_by_finding_id <> id',
    )
    op.add_column(
        'published_comments',
        sa.Column('thread_resolved_by_bot', sa.Boolean(), server_default='false', nullable=False),
    )
    # Кто закрыл тред до этой ревизии, база не знает. Закрытые считаем закрытыми
    # ботом: «человек» навсегда заглушил бы вернувшийся critical и пропустил его
    # мимо merge gate, а «бот» оставляет поведение до ревизии - замечание
    # публикуется заново.
    op.execute('UPDATE published_comments SET thread_resolved_by_bot = thread_resolved')


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_column('published_comments', 'thread_resolved_by_bot')
    op.drop_constraint('ck_findings_covered_by_other', 'findings', type_='check')
    op.drop_constraint('findings_covered_by_finding_id_fkey', 'findings', type_='foreignkey')
    op.drop_column('findings', 'covered_by_finding_id')
