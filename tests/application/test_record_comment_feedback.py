from datetime import UTC, datetime
from uuid import UUID

from app.application.use_cases.record_comment_feedback import record_comment_feedback
from app.domain.entities import PublishedComment
from app.domain.enums import CommentKind

from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def test_feedback_increments_counters() -> None:
    uow = FakeUnitOfWork()
    uow.published_comments.add(
        PublishedComment(
            id=UUID(int=1),
            review_run_id=UUID(int=2),
            provider_comment_id="gh-99",
            kind=CommentKind.INLINE,
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    assert record_comment_feedback(uow, "gh-99", positive=True) is True
    comment = uow.published_comments.find_by_provider_comment_id("gh-99")
    assert comment is not None
    assert comment.feedback_positive == 1
