"""context_payloads: тело пакета хранится ограниченный срок

Change `context-payload-retention`. В теле пакета дословно лежит чужой исходный код, и таблица
растёт с каждым прогоном. По истечении срока хранения воркер стирает тело (`body` → NULL) и
ставит `body_expired_at`; метаданные — файлы, уровни, токены, дайджест — остаются: история
прогона не удаляется неявно (`review-data-model`).

- `body` становится nullable, `body_expired_at` — новая колонка; check-ограничение держит их
  вместе: тела нет ровно тогда, когда отмечено время его стирания.
- Частичный индекс `(created_at) WHERE body IS NOT NULL` — проход воркера выбирает самые старые
  живые тела, не читая уже стёртые строки.

Блокировки. Прогон миграций — одна транзакция (`alembic/env.py`), поэтому `ADD COLUMN`,
`DROP NOT NULL` и `ADD CONSTRAINT` держат ACCESS EXCLUSIVE на `context_payloads` до коммита, а
check-ограничение проверяет все строки под ней. Таблицу не перезаписывает ни один шаг: колонка
без умолчания и `DROP NOT NULL` меняют только каталог. Индекс — обычный `CREATE INDEX`
(`CONCURRENTLY` в транзакции нельзя). На стенде таблица небольшая, и всё укладывается в
`lock_timeout`; в таблицу пишет только воркер, один раз за прогон.

Откат возвращает `NOT NULL`: стёртым телам ставится пустой объект `{}` — стёртое не
восстановить.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-06 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade схемы."""
    op.add_column(
        "context_payloads",
        sa.Column("body_expired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "context_payloads",
        "body",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_context_payloads_body_expiry",
        "context_payloads",
        "(body IS NULL) = (body_expired_at IS NOT NULL)",
    )
    op.create_index(
        "ix_context_payloads_live_created_at",
        "context_payloads",
        ["created_at"],
        postgresql_where=sa.text("body IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_index(
        "ix_context_payloads_live_created_at",
        table_name="context_payloads",
        postgresql_where=sa.text("body IS NOT NULL"),
    )
    op.drop_constraint(
        "ck_context_payloads_body_expiry", "context_payloads", type_="check"
    )
    op.execute("UPDATE context_payloads SET body = '{}'::jsonb WHERE body IS NULL")
    op.alter_column(
        "context_payloads",
        "body",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )
    op.drop_column("context_payloads", "body_expired_at")
