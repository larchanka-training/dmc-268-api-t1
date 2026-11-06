"""review_runs: разбивка токенов по видам и стоимость вызовов модели

Change `run-token-cost`. Рядом с `tokens_used` — входные, выходные, кэшированные и
reasoning-токены (`bigint`) и стоимость в евро `cost_eur NUMERIC(20, 10)`: точно, с 10 знаками,
как у баланса Eurorouter (`/credits`), чтобы суммы SQL сходились с ним до последнего знака.
Колонки nullable без умолчания: пусты до
анализа и у прогонов, записанных раньше; старый код их не читает, поэтому expand без второго
шага. `ADD COLUMN` без умолчания меняет только каталог — таблицу не переписывает, блокировка
короткая. CHECK `ck_review_runs_usage`: колонки расхода пишутся вместе и не
отрицательны; на пустых колонках проверка мгновенная.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-06 10:46:00.221112

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0018'
down_revision: str | Sequence[str] | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens")


def upgrade() -> None:
    """Колонки расхода модели на review_runs."""
    for column in _TOKEN_COLUMNS:
        op.add_column('review_runs', sa.Column(column, sa.BigInteger(), nullable=True))
    op.add_column('review_runs', sa.Column('cost_eur', sa.Numeric(20, 10), nullable=True))
    op.create_check_constraint(
        'ck_review_runs_usage',
        'review_runs',
        '(input_tokens IS NULL) = (output_tokens IS NULL)'
        ' AND (input_tokens IS NULL) = (cached_tokens IS NULL)'
        ' AND (input_tokens IS NULL) = (reasoning_tokens IS NULL)'
        ' AND (input_tokens IS NULL) = (cost_eur IS NULL)'
        ' AND (input_tokens IS NULL OR (tokens_used IS NOT NULL AND input_tokens >= 0'
        ' AND output_tokens >= 0 AND cached_tokens >= 0 AND reasoning_tokens >= 0 AND cost_eur >= 0))',
    )


def downgrade() -> None:
    """Убрать колонки расхода модели."""
    op.drop_constraint('ck_review_runs_usage', 'review_runs', type_='check')
    op.drop_column('review_runs', 'cost_eur')
    for column in reversed(_TOKEN_COLUMNS):
        op.drop_column('review_runs', column)
