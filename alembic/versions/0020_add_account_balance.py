"""Баланс аккаунта: журнал зачислений, плательщик репозитория, резерв на прогоне

Change `account-balance`. Баланс аккаунта — сумма его зачислений минус расход прогонов, где он
плательщик (design D1): зачисления — новая таблица-журнал `balance_credits`, расход уже лежит
в `review_runs.cost_eur`.

- `balance_credits`: аккаунт, сумма `NUMERIC(20, 10)` (как `cost_eur`), заметка, кто зачислил,
  `created_at`/`updated_at`, как у всех таблиц; внешний ключ на `users` с RESTRICT, индекс по аккаунту.
- `repositories.payer_user_id` — плательщик репозитория; частичный индекс по нему —
  репозитории, за которые платит пользователь (`GET /auth/me`).
- `review_runs.payer_user_id`, `estimated_cost_eur` (резерв, design D2) и `coverage_reason`
  (нативный enum `review_coverage_reason`, сейчас одно значение `balance`); CHECK
  `ck_review_runs_estimate`: оценка только с плательщиком и не отрицательна; частичный индекс
  по плательщику — проверка баланса суммирует его прогоны.

Все колонки nullable без умолчания: меняется только каталог, таблицы не переписываются, старый
код их не читает. `ADD CONSTRAINT … CHECK` проверяет `review_runs` под ACCESS EXCLUSIVE, а
`CREATE INDEX` без `CONCURRENTLY` держит SHARE: на стенде таблицы маленькие, это миллисекунды
в пределах `lock_timeout`.

Откат удаляет таблицу, колонки и тип; расход прогонов остаётся, зачисления теряются.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-07 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COVERAGE_REASON = postgresql.ENUM("balance", name="review_coverage_reason", create_type=False)


def upgrade() -> None:
    """Upgrade схемы."""
    op.create_table(
        "balance_credits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("amount_eur", sa.Numeric(20, 10), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("credited_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_balance_credits_user_id"), "balance_credits", ["user_id"])

    op.add_column("repositories", sa.Column("payer_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "repositories_payer_user_id_fkey",
        "repositories",
        "users",
        ["payer_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_repositories_payer_user_id",
        "repositories",
        ["payer_user_id"],
        postgresql_where=sa.text("payer_user_id IS NOT NULL"),
    )

    _COVERAGE_REASON.create(op.get_bind())
    op.add_column("review_runs", sa.Column("payer_user_id", sa.Uuid(), nullable=True))
    op.add_column("review_runs", sa.Column("estimated_cost_eur", sa.Numeric(20, 10), nullable=True))
    op.add_column("review_runs", sa.Column("coverage_reason", _COVERAGE_REASON, nullable=True))
    op.create_foreign_key(
        "review_runs_payer_user_id_fkey",
        "review_runs",
        "users",
        ["payer_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_review_runs_estimate",
        "review_runs",
        "estimated_cost_eur IS NULL OR (payer_user_id IS NOT NULL AND estimated_cost_eur >= 0)",
    )
    op.create_index(
        "ix_review_runs_payer_user_id",
        "review_runs",
        ["payer_user_id"],
        postgresql_where=sa.text("payer_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_index(
        "ix_review_runs_payer_user_id",
        table_name="review_runs",
        postgresql_where=sa.text("payer_user_id IS NOT NULL"),
    )
    op.drop_constraint("ck_review_runs_estimate", "review_runs", type_="check")
    op.drop_constraint("review_runs_payer_user_id_fkey", "review_runs", type_="foreignkey")
    op.drop_column("review_runs", "coverage_reason")
    op.drop_column("review_runs", "estimated_cost_eur")
    op.drop_column("review_runs", "payer_user_id")
    # autogenerate тип не удаляет: следующий upgrade упал бы на создании существующего.
    _COVERAGE_REASON.drop(op.get_bind())

    op.drop_index(
        "ix_repositories_payer_user_id",
        table_name="repositories",
        postgresql_where=sa.text("payer_user_id IS NOT NULL"),
    )
    op.drop_constraint("repositories_payer_user_id_fkey", "repositories", type_="foreignkey")
    op.drop_column("repositories", "payer_user_id")

    op.drop_index(op.f("ix_balance_credits_user_id"), table_name="balance_credits")
    op.drop_table("balance_credits")
