from datetime import UTC, datetime
from uuid import UUID

from app.application.merge_gate_publish import DISABLED_REVIEW_CHECK
from app.application.sync_thread_resolution import (
    apply_review_thread_webhook,
    handle_review_thread_webhook,
    refresh_merge_gate_for_merge_request,
)
from app.domain.entities import (
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    Repository,
    ReviewRun,
)
from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)

from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.vcs_gateway import RecordingVcsGateway

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


def test_webhook_reopening_a_thread_drops_the_bot_mark() -> None:
    """Тред, который закрыл бот, открыли заново: следующее закрытие - уже не ботом."""
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
            thread_resolved=True,
            thread_resolved_by_bot=True,
        )
    )

    assert apply_review_thread_webhook(
        {"action": "unresolved", "thread": {"comments": [{"id": 77}]}}, uow=uow, now=lambda: NOW
    )
    assert apply_review_thread_webhook(
        {"action": "resolved", "thread": {"comments": [{"id": 77}]}}, uow=uow, now=lambda: NOW
    )

    row = uow.published_comments.find_by_provider_comment_id("77")
    assert row is not None
    assert (row.thread_resolved, row.thread_resolved_by_bot) == (True, False)


def test_repeated_resolve_webhook_keeps_the_bot_mark_of_an_old_record() -> None:
    """Метка бота у старых записей сохраняется: повторная доставка `resolved`
    по уже закрытому треду не превращает закрытие ботом в закрытие человеком."""
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
            thread_resolved=True,
            thread_resolved_by_bot=True,
        )
    )

    assert apply_review_thread_webhook(
        {"action": "resolved", "thread": {"comments": [{"id": 77}]}}, uow=uow, now=lambda: NOW
    )

    row = uow.published_comments.find_by_provider_comment_id("77")
    assert row is not None
    assert row.thread_resolved_by_bot is True


def test_merge_gate_refresh_sees_a_critical_whose_inline_id_was_lost() -> None:
    """#185: ревью прогона на head опубликовано, id inline `critical` не получен -
    закрытие другого треда не снимает блокировку."""
    uow = FakeUnitOfWork()
    mr_id, run_id = UUID(int=3), UUID(int=2)
    uow.review_runs.add(
        ReviewRun(
            id=run_id,
            merge_request_id=mr_id,
            head_sha="sha1",
            status=ReviewRunStatus.COMPLETED,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.findings.add(
        Finding(
            id=UUID(int=10),
            review_run_id=run_id,
            anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=1),
            category=FindingCategory.SECURITY,
            severity=FindingSeverity.CRITICAL,
            message="инъекция",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.published_comments.add(
        PublishedComment(
            id=UUID(int=11),
            review_run_id=run_id,
            provider_comment_id="500",
            kind=CommentKind.SUMMARY,
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    vcs = RecordingVcsGateway()

    refresh_merge_gate_for_merge_request(
        mr_id,
        repo_full_name="o/r",
        pr_number=7,
        head_sha="sha1",
        installation_id=1,
        uow=uow,
        vcs=vcs,
        now=NOW,
    )

    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_thread_webhook_of_a_disconnected_repository_sets_the_disabled_check() -> None:
    """Выключенный репозиторий (design D6): человек закрыл один из двух `critical` тредов —
    тред отмечен, а на head ставится «Ревью выключено в сервисе», а не пересчёт по тредам
    (тот дал бы `failure` из-за второго открытого треда)."""
    uow = FakeUnitOfWork()
    repo_id, mr_id, run_id, finding_id = UUID(int=1), UUID(int=3), UUID(int=2), UUID(int=10)
    uow.repositories.add(
        Repository(
            id=repo_id,
            provider=Provider.GITHUB,
            provider_id="42",
            full_name="o/r",
            default_branch="main",
            auto_review_enabled=True,
            created_at=NOW,
            updated_at=NOW,
            connected_at=None,
        )
    )
    uow.merge_requests.add(
        MergeRequest(
            id=mr_id,
            repository_id=repo_id,
            number=7,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="main",
            head_sha="sha1",
            state=MergeRequestState.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.review_runs.add(
        ReviewRun(
            id=run_id,
            merge_request_id=mr_id,
            head_sha="sha1",
            status=ReviewRunStatus.COMPLETED,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.findings.add(
        Finding(
            id=finding_id,
            review_run_id=run_id,
            anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=1),
            category=FindingCategory.SECURITY,
            severity=FindingSeverity.CRITICAL,
            message="инъекция",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.published_comments.add(
        PublishedComment(
            id=UUID(int=11),
            review_run_id=run_id,
            provider_comment_id="77",
            kind=CommentKind.INLINE,
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
            finding_id=finding_id,
        )
    )
    second_finding_id = UUID(int=12)
    uow.findings.add(
        Finding(
            id=second_finding_id,
            review_run_id=run_id,
            anchor=DiffAnchor(file_path="b.py", side=DiffSide.NEW, new_line=2),
            category=FindingCategory.SECURITY,
            severity=FindingSeverity.CRITICAL,
            message="ещё инъекция",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.published_comments.add(
        PublishedComment(
            id=UUID(int=13),
            review_run_id=run_id,
            provider_comment_id="78",
            kind=CommentKind.INLINE,
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
            finding_id=second_finding_id,
        )
    )
    vcs = RecordingVcsGateway()
    payload = {
        "action": "resolved",
        "thread": {"comments": [{"id": 77}]},
        "pull_request": {"number": 7, "head": {"sha": "sha1"}},
        "repository": {"full_name": "o/r", "id": 42},
        "installation": {"id": 1},
    }

    assert handle_review_thread_webhook(payload, uow=uow, vcs=vcs, now=lambda: NOW) is True

    row = uow.published_comments.find_by_provider_comment_id("77")
    assert row is not None and row.thread_resolved is True
    assert vcs.checks == [DISABLED_REVIEW_CHECK]
