from datetime import UTC, datetime
from uuid import UUID

from app.application.sync_thread_resolution import apply_review_thread_webhook
from app.domain.entities import PublishedComment
from app.domain.enums import CommentKind

from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def test_webhook_resolved_marks_inline_thread() -> None:
    uow = FakeUnitOfWork()
    uow.published_comments.add(
        PublishedComment(
            id=UUID(int=1),
            review_run_id=UUID(int=2),
            provider_comment_id="77",
            kind=CommentKind.INLINE,
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    payload = {
        "action": "resolved",
        "thread": {"comments": [{"id": 77}, {"id": 88}]},
    }

    assert apply_review_thread_webhook(payload, uow=uow, now=lambda: NOW) is True

    row = uow.published_comments.find_by_provider_comment_id("77")
    assert row is not None
    assert row.thread_resolved is True
