"""context_payloads: без старых пакетов и без индекса по дайджесту

Пакеты контекста теперь пишутся с вычищенными секретами (#28). Старые строки
лежат дословно, секреты в них остаются; их никто не читает, поэтому они
удаляются, а не переписываются. Переиспользования пакета по дайджесту нет —
индекс `ix_context_payloads_digest` не нужен.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-31 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0015'
down_revision: str | Sequence[str] | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    op.execute('DELETE FROM context_payloads')
    op.drop_index('ix_context_payloads_digest', table_name='context_payloads')


def downgrade() -> None:
    """Downgrade схемы: индекс возвращается, удалённые пакеты — нет."""
    op.create_index('ix_context_payloads_digest', 'context_payloads', ['content_sha256'])
