"""Ревизия 0012: треды, закрытые до неё, считаются закрытыми ботом."""

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from alembic import command
from app.domain.enums import CommentKind

from ..conftest import TEST_DATABASE_URL, requires_db
from .test_constraints import add_summary, make_mr, make_repo, make_run

pytestmark = [pytest.mark.integration, requires_db]


def _config() -> Config:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


def test_threads_resolved_before_the_revision_count_as_resolved_by_bot(clean_db) -> None:
    """Кто закрыл старый тред, неизвестно. «Человек» заглушил бы вернувшийся critical
    навсегда и пропустил его мимо merge gate; «бот» возвращает поведение до ревизии —
    замечание публикуется заново."""
    with Session(clean_db) as session:
        mr = make_mr(session, make_repo(session))
        first, second = make_run(session, mr), make_run(session, mr, head="def456")
        add_summary(session, first, provider_comment_id="closed", kind=CommentKind.INLINE)
        add_summary(session, second, provider_comment_id="open", kind=CommentKind.INLINE)
        session.execute(
            text("UPDATE published_comments SET thread_resolved = true WHERE provider_comment_id = 'closed'")
        )
        session.commit()
    config = _config()
    try:
        command.downgrade(config, "0011")
    finally:
        command.upgrade(config, "head")
    with clean_db.connect() as conn:
        rows = dict(
            conn.execute(text("SELECT provider_comment_id, thread_resolved_by_bot FROM published_comments")).all()
        )
    assert rows == {"closed": True, "open": False}
