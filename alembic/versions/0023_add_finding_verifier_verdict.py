"""вердикт верификатора у замечания прогона

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-10 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0023'
down_revision: str | Sequence[str] | None = '0022'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERDICT = postgresql.ENUM('keep', 'drop', name='verifier_verdict')


def upgrade() -> None:
    """Upgrade схемы: два пустых столбца без значения по умолчанию.

    Таблицу они не перезаписывают, перенос данных не нужен: прежние замечания остаются
    без вердикта, восстановить его нечем. Ограничение проходит по всем строкам, но
    у них обе колонки пусты, а блокировка короткая.
    """
    _VERDICT.create(op.get_bind())
    op.add_column(
        'findings',
        sa.Column('verifier_verdict', postgresql.ENUM(name='verifier_verdict', create_type=False), nullable=True),
    )
    op.add_column('findings', sa.Column('verifier_reason', sa.Text(), nullable=True))
    op.create_check_constraint(
        'ck_findings_verifier_verdict', 'findings', '(verifier_verdict IS NULL) = (verifier_reason IS NULL)'
    )


def downgrade() -> None:
    """Downgrade схемы: вердикты теряются, замечания остаются."""
    op.drop_constraint('ck_findings_verifier_verdict', 'findings', type_='check')
    op.drop_column('findings', 'verifier_reason')
    op.drop_column('findings', 'verifier_verdict')
    _VERDICT.drop(op.get_bind())
