"""Сессия Checkout Stripe на зачислении: одно зачисление на оплату

Пополнение через Stripe (change `stripe-topup`, D6) зачисляет оплату по вебхуку, а Stripe
доставляет событие повторно и шлёт два события одной сессии при асинхронной оплате. Колонка
`balance_credits.stripe_checkout_session_id` хранит id сессии Checkout, частичный уникальный
индекс оставляет на сессию одно зачисление; у ручных зачислений колонка пуста.

Колонка nullable без умолчания — меняется только каталог. `CREATE INDEX` без `CONCURRENTLY`
держит SHARE на `balance_credits`: таблица маленькая, это миллисекунды в пределах
`lock_timeout`, а пишет в неё только администратор.

Откат удаляет индекс и колонку: зачисления остаются, связь со сессиями Stripe теряется.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-10 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0022'
down_revision: str | Sequence[str] | None = '0021'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Колонка id сессии и частичный уникальный индекс."""
    op.add_column("balance_credits", sa.Column("stripe_checkout_session_id", sa.String(length=255), nullable=True))
    op.create_index(
        "uq_balance_credits_stripe_checkout_session_id",
        "balance_credits",
        ["stripe_checkout_session_id"],
        unique=True,
        postgresql_where=sa.text("stripe_checkout_session_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Удалить индекс и колонку."""
    op.drop_index("uq_balance_credits_stripe_checkout_session_id", table_name="balance_credits")
    op.drop_column("balance_credits", "stripe_checkout_session_id")
