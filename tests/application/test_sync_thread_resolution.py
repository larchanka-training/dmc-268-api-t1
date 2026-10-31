from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.application.merge_gate_publish import (
    DISABLED_REVIEW_CHECK,
    build_retarget_check,
)
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


# --- #222: пересчёт gate - только по завершённому ревью head ----------------------

MR_ID = UUID(int=3)


def _run(run_id: int, status: ReviewRunStatus, *, at: int, base: str = "base1") -> ReviewRun:
    moment = NOW.replace(minute=at)
    return ReviewRun(
        id=UUID(int=run_id),
        merge_request_id=MR_ID,
        head_sha="sha1",
        base_sha=base,
        status=status,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=moment,
        created_at=moment,
        updated_at=moment,
    )


def _refresh(uow: FakeUnitOfWork) -> RecordingVcsGateway:
    vcs = RecordingVcsGateway()
    refresh_merge_gate_for_merge_request(
        MR_ID,
        repo_full_name="o/r",
        pr_number=7,
        head_sha="sha1",
        installation_id=1,
        uow=uow,
        vcs=vcs,
        now=NOW,
    )
    return vcs


def test_closing_a_thread_before_the_head_is_reviewed_sets_no_gate() -> None:
    """Обход: push, ревью нового head ещё идёт, автор закрывает тред - `success` на
    непроверенный коммит не выставляется."""
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, at=1))

    assert _refresh(uow).checks == []


def test_closing_a_thread_after_the_review_of_a_new_base_failed_sets_no_gate() -> None:
    """Целевую ветку сменили, ревью нового диффа упало: прогон на старой базе
    `success` уже не даёт."""
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.COMPLETED, at=1, base="old"))
    uow.review_runs.add(_run(2, ReviewRunStatus.FAILED, at=2, base="new"))

    assert _refresh(uow).checks == []


def test_closing_a_thread_after_a_completed_review_of_the_head_sets_the_gate() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.CANCELLED, at=1, base="old"))
    uow.review_runs.add(_run(2, ReviewRunStatus.COMPLETED, at=2, base="new"))

    (check,) = _refresh(uow).checks
    assert check.conclusion == "success"


class _RetargetDuringGateVcs(RecordingVcsGateway):
    """На первый POST check run гонкой приходится смена ветки: приём коммитит новый прогон."""

    def __init__(self, uow: FakeUnitOfWork) -> None:
        super().__init__()
        self._uow = uow

    def set_merge_gate_check(self, *args: Any) -> None:
        super().set_merge_gate_check(*args)
        if len(self.checks) == 1:
            self._uow.review_runs.add(_run(2, ReviewRunStatus.QUEUED, at=2, base="new"))


def test_retarget_racing_a_thread_refresh_ends_in_failure() -> None:
    """Пересчёт прочитал `completed` на старой ветке до коммита приёма и выставил
    `success` после его блокировки - перечитав последний прогон, он её возвращает."""
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.COMPLETED, at=1, base="old"))
    vcs = _RetargetDuringGateVcs(uow)
    refresh_merge_gate_for_merge_request(
        MR_ID,
        repo_full_name="o/r",
        pr_number=7,
        head_sha="sha1",
        installation_id=1,
        uow=uow,
        vcs=vcs,
        now=NOW,
    )

    assert [c.conclusion for c in vcs.checks] == ["success", "failure"]
    assert vcs.checks[-1] == build_retarget_check()


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
