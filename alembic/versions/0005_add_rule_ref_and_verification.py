"""ссылка замечания на правило владельца и итог верификации прогона

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0005'
down_revision: str | Sequence[str] | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERIFICATION = postgresql.ENUM(
    "verified", "not_required", "unavailable", name="review_verification"
)


def upgrade() -> None:
    """Upgrade схемы."""
    # Пункт правила владельца, по которому выпущено замечание. Три nullable-
    # колонки, а не JSONB: правило «всё или ничего» выражается в базе, а
    # уникальность привязки `uq_findings_anchor` от ссылки не зависит.
    op.add_column("findings", sa.Column("rule_name", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("rule_check", sa.Integer(), nullable=True))
    op.add_column("findings", sa.Column("rule_check_text", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_findings_rule_ref",
        "findings",
        "(rule_name IS NULL AND rule_check IS NULL AND rule_check_text IS NULL)"
        " OR (rule_name IS NOT NULL AND rule_check IS NOT NULL"
        " AND rule_check_text IS NOT NULL AND rule_check >= 1)",
    )

    # Итог второго прохода модели. Nullable: у прогонов, записанных до
    # появления верификации, итога нет, и проставить его нечем.
    _VERIFICATION.create(op.get_bind())
    op.add_column(
        "review_runs",
        sa.Column(
            "verification",
            postgresql.ENUM(name="review_verification", create_type=False),
            nullable=True,
        ),
    )
    # Счётчик со значением по умолчанию: существующие прогоны получают 0, а
    # не NULL, - верификатор у них ничего не отбрасывал.
    op.add_column(
        "review_runs",
        sa.Column(
            "verifier_dropped_findings", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_review_runs_verifier_dropped",
        "review_runs",
        "verifier_dropped_findings >= 0",
    )


def downgrade() -> None:
    """Downgrade схемы."""
    op.drop_constraint("ck_review_runs_verifier_dropped", "review_runs", type_="check")
    op.drop_column("review_runs", "verifier_dropped_findings")
    op.drop_column("review_runs", "verification")
    # Autogenerate тип не удаляет: без этого следующий upgrade упадёт на
    # создании существующего типа.
    _VERIFICATION.drop(op.get_bind())
    op.drop_constraint("ck_findings_rule_ref", "findings", type_="check")
    op.drop_column("findings", "rule_check_text")
    op.drop_column("findings", "rule_check")
    op.drop_column("findings", "rule_name")
