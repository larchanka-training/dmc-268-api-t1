"""published_comments: треды и фидбек

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE comment_kind ADD VALUE IF NOT EXISTS 'thread_reply'")
    op.add_column(
        "published_comments",
        sa.Column("thread_resolved", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "published_comments",
        sa.Column("in_reply_to_provider_comment_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "published_comments",
        sa.Column("feedback_positive", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "published_comments",
        sa.Column("feedback_negative", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("published_comments", "feedback_negative")
    op.drop_column("published_comments", "feedback_positive")
    op.drop_column("published_comments", "in_reply_to_provider_comment_id")
    op.drop_column("published_comments", "thread_resolved")
