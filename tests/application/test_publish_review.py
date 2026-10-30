"""Публикация ревью: комментарии, треды, merge gate."""

import itertools
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from app.application.merge_gate_publish import (
    SETTLE_ATTEMPTS,
    build_failed_review_check,
    build_retarget_check,
    settle_merge_gate,
)
from app.application.ports.llm_gateway import LlmReviewResult
from app.application.ports.unit_of_work import ActiveRunConflict
from app.application.ports.vcs_review import InlineCommentDraft, ReviewThreadState
from app.application.publish_review import publish_review
from app.domain.entities import (
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    ReviewJob,
    ReviewRun,
    RuleRef,
)
from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.vcs_errors import VcsError, VcsTransientError

from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.vcs_gateway import RecordingVcsGateway

NOW = datetime(2026, 10, 12, tzinfo=UTC)
MR_ID = UUID(int=3)
RUN_ID = UUID(int=2)
FINDING_ID = UUID(int=50)
ROOT_COMMENT_ID = UUID(int=51)


JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=RUN_ID,
    event_type="pull_request",
    action="synchronize",
    installation_id=1,
    repository_provider_id="1",
    repository_full_name="owner/repo",
    pull_request_number=7,
    head_sha="sha2",
    base_sha="base",
)


def _seed_previous_critical_thread(uow: FakeUnitOfWork) -> None:
    uow.merge_requests.add(
        MergeRequest(
            id=MR_ID,
            repository_id=UUID(int=9),
            number=7,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="develop",
            head_sha="sha1",
            state=MergeRequestState.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    prev_run = ReviewRun(
        id=UUID(int=4),
        merge_request_id=MR_ID,
        head_sha="sha1",
        status=ReviewRunStatus.COMPLETED,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.review_runs.add(prev_run)
    finding = Finding(
        id=FINDING_ID,
        review_run_id=prev_run.id,
        anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=10),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.CRITICAL,
        message="критично",
        created_at=NOW,
        updated_at=NOW,
    )
    uow.findings.add(finding)
    uow.published_comments.add(
        PublishedComment(
            id=ROOT_COMMENT_ID,
            review_run_id=prev_run.id,
            finding_id=FINDING_ID,
            provider_comment_id="root-inline",
            kind=CommentKind.INLINE,
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def test_merge_gate_fails_while_critical_thread_open() -> None:
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    uow.review_runs.add(
        ReviewRun(
            id=RUN_ID,
            merge_request_id=MR_ID,
            head_sha=JOB.head_sha,
            status=ReviewRunStatus.PUBLISHING,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    vcs = RecordingVcsGateway()
    result = LlmReviewResult(model="m", tokens_used=0, findings=())

    publish_review(
        JOB,
        uow.review_runs.get(RUN_ID),  # type: ignore[arg-type]
        result,
        uow=uow,
        vcs=vcs,
        now=NOW,
        new_id=lambda: UUID(int=300),
    )

    assert vcs.checks[-1].conclusion == "failure"


def _publishing_run(uow: FakeUnitOfWork) -> ReviewRun:
    run = ReviewRun(
        id=RUN_ID,
        merge_request_id=MR_ID,
        head_sha=JOB.head_sha,
        status=ReviewRunStatus.PUBLISHING,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.review_runs.add(run)
    return run


@pytest.mark.parametrize(
    "error",
    [
        VcsTransientError("GitHub API отвечал 429/5xx 3 попытки подряд на POST /graphql"),
        VcsError("GitHub GraphQL: [{'message': 'Something went wrong'}]"),
    ],
    ids=["unavailable", "graphql-error"],
)
def test_thread_sync_failure_still_sets_the_merge_gate_from_the_database(
    error: VcsError, caplog: pytest.LogCaptureFixture
) -> None:
    """#76: сверка тредов упала после публикации - прогон не падает, gate по базе."""
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    run = _publishing_run(uow)
    vcs = RecordingVcsGateway(threads_error=error)

    with caplog.at_level(logging.WARNING, logger="app.application.publish_review"):
        publish_review(
            JOB,
            run,
            LlmReviewResult(model="m", tokens_used=0, findings=()),
            uow=uow,
            vcs=vcs,
            now=NOW,
            new_id=lambda: UUID(int=302),
        )

    (check,) = vcs.checks
    assert check.conclusion == "failure"
    assert len(vcs.publications) == 1
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(RUN_ID) in record.getMessage()
    assert record.exc_info is not None and record.exc_info[1] is error


def test_thread_sync_programming_error_is_not_swallowed() -> None:
    """Не сбой хостинга, а ошибка в коде - прогон падает, как раньше."""
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    run = _publishing_run(uow)
    vcs = RecordingVcsGateway(threads_error=AttributeError("опечатка"))

    with pytest.raises(AttributeError):
        publish_review(
            JOB,
            run,
            LlmReviewResult(model="m", tokens_used=0, findings=()),
            uow=uow,
            vcs=vcs,
            now=NOW,
            new_id=lambda: UUID(int=303),
        )


def test_inline_comment_is_attributed_from_the_stored_rule_reference() -> None:
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    run = ReviewRun(
        id=RUN_ID,
        merge_request_id=MR_ID,
        head_sha=JOB.head_sha,
        status=ReviewRunStatus.PUBLISHING,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.review_runs.add(run)
    uow.findings.add(
        Finding(
            id=UUID(int=60),
            review_run_id=RUN_ID,
            anchor=DiffAnchor(file_path="app/y.py", side=DiffSide.NEW, new_line=3),
            category=FindingCategory.READABILITY,
            severity=FindingSeverity.LOW,
            message="Имя не глагол.",
            created_at=NOW,
            updated_at=NOW,
            rule=RuleRef("Naming", 1, "Имя функции - глагол"),
        )
    )
    vcs = RecordingVcsGateway()

    publish_review(
        JOB,
        run,
        LlmReviewResult(model="m", tokens_used=0, findings=()),
        uow=uow,
        vcs=vcs,
        now=NOW,
        new_id=lambda: UUID(int=301),
    )

    ((_, (inline,)),) = vcs.publications
    assert inline.body.endswith(
        "Согласно пользовательскому правилу «Naming» (пункт 1: Имя функции - глагол): Имя не глагол."
    )


def _ids() -> Callable[[], UUID]:
    counter = itertools.count(400)
    return lambda: UUID(int=next(counter))


# --- #148: повторный прогон не дублирует замечание с тредом бота ---------------

NEW_FINDING_ID = UUID(int=80)


def _seed_prior_thread(
    uow: FakeUnitOfWork,
    *,
    severity: FindingSeverity,
    resolved: bool = False,
    resolved_by_bot: bool = False,
) -> None:
    """Тред бота с прошлого прогона на `app/x.py:10` (correctness) нужной серьёзности."""
    _seed_previous_critical_thread(uow)
    (finding,) = uow.findings.list_for_run(UUID(int=4))
    uow.findings.restore(
        [replace(f, severity=severity) if f.id == finding.id else f for f in uow.findings.snapshot()]
    )
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    uow.published_comments.update(
        replace(root, thread_resolved=resolved, thread_resolved_by_bot=resolved_by_bot)
    )


def _current_finding(
    uow: FakeUnitOfWork, *, severity: FindingSeverity, line: int = 10
) -> None:
    uow.findings.add(
        Finding(
            id=NEW_FINDING_ID,
            review_run_id=RUN_ID,
            anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=line),
            category=FindingCategory.CORRECTNESS,
            severity=severity,
            message="та же проблема",
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _on_host(*, line: int | None, resolved: bool = False) -> RecordingVcsGateway:
    return RecordingVcsGateway(
        thread_states={"root-inline": ReviewThreadState(resolved=resolved, line=line)}
    )


def _publish(uow: FakeUnitOfWork, vcs: RecordingVcsGateway) -> None:
    publish_review(
        JOB,
        _publishing_run(uow),
        LlmReviewResult(model="m", tokens_used=0, findings=()),
        uow=uow,
        vcs=vcs,
        now=NOW,
        new_id=_ids(),
    )


def _inline_drafts(vcs: RecordingVcsGateway) -> tuple[InlineCommentDraft, ...]:
    ((_, drafts),) = vcs.publications
    return drafts


def _stored_new_finding(uow: FakeUnitOfWork) -> Finding:
    (finding,) = [f for f in uow.findings.list_for_run(RUN_ID) if f.id == NEW_FINDING_ID]
    return finding


def test_rerun_with_the_same_finding_publishes_no_new_inline_comment() -> None:
    """#148: открытый тред на той же строке - замечание не повторяется, находка связана с тредом."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    assert _stored_new_finding(uow).covered_by_finding_id == FINDING_ID
    inline = [c for c in uow.published_comments.list_for_run(RUN_ID) if c.kind is CommentKind.INLINE]
    assert inline == []
    # Подавленная находка gate не ломает: критичный тред открыт - мердж заблокирован.
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_lower_severity_finding_is_covered_by_the_open_thread() -> None:
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.HIGH)
    _current_finding(uow, severity=FindingSeverity.MEDIUM)
    vcs = _on_host(line=10)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    assert _stored_new_finding(uow).covered_by_finding_id == FINDING_ID


def test_finding_that_became_critical_is_published_next_to_the_open_thread() -> None:
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.HIGH)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert (draft.file_path, draft.line) == ("app/x.py", 10)
    assert _stored_new_finding(uow).covered_by_finding_id is None
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_outdated_thread_does_not_suppress_the_finding() -> None:
    """GitHub отдаёт `line: null` у устаревшего треда: к нынешней строке он не привязан."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=None)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert (draft.file_path, draft.line) == ("app/x.py", 10)


def test_thread_is_matched_by_its_current_line_on_the_host() -> None:
    """Над строкой добавили две строки: тред с 10-й переехал на 12-ю, туда же и находка."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL, line=12)
    vcs = _on_host(line=12)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    assert _stored_new_finding(uow).covered_by_finding_id == FINDING_ID


def test_stored_line_alone_does_not_match_a_moved_thread() -> None:
    """Тред уехал на 12-ю: находка на 10-й - другое место, публикуется."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL, line=10)
    vcs = _on_host(line=12)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert draft.line == 10


def test_thread_resolved_by_a_human_suppresses_the_finding() -> None:
    """#169, п. 3а: тред закрыл человек - замечание повторно не публикуется."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL, resolved=True)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    assert _stored_new_finding(uow).covered_by_finding_id == FINDING_ID
    (check,) = vcs.checks
    assert check.conclusion == "success"


def test_thread_resolved_on_the_host_without_a_webhook_counts_as_resolved_by_a_human() -> None:
    """В базе тред открыт, на GitHub его закрыл человек: сверка до публикации это видит."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.HIGH)
    _current_finding(uow, severity=FindingSeverity.HIGH)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    assert (root.thread_resolved, root.thread_resolved_by_bot) == (True, False)


def test_critical_finding_escalated_past_a_human_resolved_thread_is_published() -> None:
    """#169, п. 3а: исключение - критичное замечание, ставшее серьёзнее, публикуется в любом случае."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.HIGH, resolved=True)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert draft.line == 10
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_non_critical_escalation_past_a_human_resolved_thread_is_not_published() -> None:
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.LOW, resolved=True)
    _current_finding(uow, severity=FindingSeverity.HIGH)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()


def test_finding_returns_after_the_bot_resolved_its_thread_as_fixed() -> None:
    """Тред закрыл сам бот как исправленный, а проблема вернулась - замечание публикуется снова."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(
        uow, severity=FindingSeverity.CRITICAL, resolved=True, resolved_by_bot=True
    )
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert draft.line == 10
    assert _stored_new_finding(uow).covered_by_finding_id is None
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_host_thread_state_is_fetched_once_per_run() -> None:
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10)

    _publish(uow, vcs)

    assert vcs.thread_state_requests == [frozenset({"root-inline"})]


def test_without_host_thread_state_every_finding_is_published(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Сверка не удалась: текущая строка треда неизвестна - лучше повтор, чем потерянное замечание."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = RecordingVcsGateway(threads_error=VcsTransientError("HTTP 502 на /graphql"))

    with caplog.at_level(logging.WARNING, logger="app.application.publish_review"):
        _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert draft.line == 10
    (check,) = vcs.checks
    assert check.conclusion == "failure"


CRITICAL_FINDING_ID = UUID(int=90)


def _run_with_critical_finding(uow: FakeUnitOfWork) -> ReviewRun:
    """Первый прогон PR: базы тредов нет, у прогона одна находка `critical`."""
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    uow.findings.add(
        Finding(
            id=CRITICAL_FINDING_ID,
            review_run_id=RUN_ID,
            anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=10),
            category=FindingCategory.SECURITY,
            severity=FindingSeverity.CRITICAL,
            message="SQL-инъекция",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return run


def _seed_merge_request(uow: FakeUnitOfWork) -> None:
    uow.merge_requests.add(
        MergeRequest(
            id=MR_ID,
            repository_id=UUID(int=9),
            number=7,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="develop",
            head_sha=JOB.head_sha,
            state=MergeRequestState.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _publish_first_run(uow: FakeUnitOfWork, run: ReviewRun, vcs: RecordingVcsGateway) -> None:
    publish_review(
        JOB,
        run,
        LlmReviewResult(model="m", tokens_used=0, findings=()),
        uow=uow,
        vcs=vcs,
        now=NOW,
        new_id=_ids(),
    )


def test_the_review_carries_the_marker_of_its_run() -> None:
    """По маркеру адаптер находит своё ревью, если ответ на POST потерялся (#185)."""
    uow = FakeUnitOfWork()
    vcs = RecordingVcsGateway()

    _publish_first_run(uow, _run_with_critical_finding(uow), vcs)

    assert vcs.review_run_ids == [RUN_ID]


def test_lost_inline_ids_keep_the_summary_and_block_the_merge() -> None:
    """#185: id inline не получены - сводка записана, gate видит `critical` без треда в базе."""
    uow = FakeUnitOfWork()
    vcs = RecordingVcsGateway(inline_ids_lost=True)

    _publish_first_run(uow, _run_with_critical_finding(uow), vcs)

    assert [(c.kind, c.provider_comment_id) for c in uow.published_comments.list_for_run(RUN_ID)] == [
        (CommentKind.SUMMARY, "summary-1")
    ]
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_merge_gate_failure_after_publishing_does_not_fail_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#185: check run не выставился - ревью уже на GitHub, повтор дал бы второе; warning в лог."""
    uow = FakeUnitOfWork()
    error = VcsTransientError("GitHub API отвечал 429/5xx 3 попытки подряд на POST /check-runs")
    vcs = RecordingVcsGateway(check_error=error)

    with caplog.at_level(logging.WARNING, logger="app.application.publish_review"):
        _publish_first_run(uow, _run_with_critical_finding(uow), vcs)

    assert len(vcs.publications) == 1
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(RUN_ID) in record.getMessage()
    assert record.exc_info is not None and record.exc_info[1] is error


# --- #222: ревью упавшего прогона не дублируется следующим --------------------

ORPHAN_RUN_ID = UUID(int=4)


def _seed_orphan(uow: FakeUnitOfWork, *, failed_stage: ReviewRunStatus = ReviewRunStatus.PUBLISHING) -> None:
    """Прошлый прогон упал на публикации, не записав `published_comments`: его
    находка `critical` на `app/x.py:10` осталась только на GitHub."""
    _seed_previous_critical_thread(uow)
    uow.published_comments.restore([])
    run = uow.review_runs.get(ORPHAN_RUN_ID)
    assert run is not None
    # Прогон кладётся сразу упавшим: переход `completed` → `failed` фейк не пропустит.
    uow.review_runs.add(replace(run, status=ReviewRunStatus.FAILED, failed_stage=failed_stage))


def test_review_of_a_run_that_died_while_publishing_covers_the_same_finding() -> None:
    """Ревью упавшего прогона найдено по маркеру: его комментарии записаны, своё замечание не постится."""
    uow = FakeUnitOfWork()
    _seed_orphan(uow)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = RecordingVcsGateway(stored_reviews={ORPHAN_RUN_ID})
    vcs.thread_states = {"orphan-inline-app/x.py-10": ReviewThreadState(resolved=False, line=10)}

    _publish(uow, vcs)

    assert vcs.found_review_requests == [ORPHAN_RUN_ID]
    adopted = {(c.kind, c.provider_comment_id) for c in uow.published_comments.list_for_run(ORPHAN_RUN_ID)}
    assert adopted == {
        (CommentKind.SUMMARY, "orphan-summary"),
        (CommentKind.INLINE, "orphan-inline-app/x.py-10"),
    }
    assert _inline_drafts(vcs) == ()
    assert _stored_new_finding(uow).covered_by_finding_id == FINDING_ID
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_a_run_that_died_before_github_stored_its_review_adopts_nothing() -> None:
    """На GitHub ревью упавшего нет: записей не появляется, замечание публикуется."""
    uow = FakeUnitOfWork()
    _seed_orphan(uow)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = RecordingVcsGateway()

    _publish(uow, vcs)

    assert vcs.found_review_requests == [ORPHAN_RUN_ID]
    assert uow.published_comments.list_for_run(ORPHAN_RUN_ID) == []
    assert [(d.file_path, d.line) for d in _inline_drafts(vcs)] == [("app/x.py", 10)]


def test_a_run_that_failed_before_publishing_is_not_looked_up() -> None:
    """Упавший на анализе прогон ревью не постил: GitHub не спрашиваем."""
    uow = FakeUnitOfWork()
    _seed_orphan(uow, failed_stage=ReviewRunStatus.ANALYSING)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = RecordingVcsGateway(stored_reviews={ORPHAN_RUN_ID})

    _publish(uow, vcs)

    assert vcs.found_review_requests == []


def test_failed_lookup_of_an_orphan_review_does_not_stop_publishing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Поиск ревью упавшего прогона - best-effort: сбой GitHub в лог, ревью публикуется."""
    uow = FakeUnitOfWork()
    _seed_orphan(uow)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    error = VcsTransientError("GitHub API недоступен на GET /reviews")
    vcs = RecordingVcsGateway(stored_reviews={ORPHAN_RUN_ID}, find_review_error=error)

    with caplog.at_level(logging.WARNING, logger="app.application.publish_review"):
        _publish(uow, vcs)

    assert len(vcs.publications) == 1
    assert uow.published_comments.list_for_run(ORPHAN_RUN_ID) == []
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(ORPHAN_RUN_ID) in record.getMessage()
    assert record.exc_info is not None and record.exc_info[1] is error


def test_run_cancelled_before_publishing_posts_neither_review_nor_gate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Целевую ветку сменили до POST ревью (#222, ревью #225): ревью по диффу против
    старой ветки не публикуется - его замечания стали бы тредами в gate нового
    прогона, - и check run не выставляется."""
    uow = FakeUnitOfWork()
    run = _run_with_critical_finding(uow)
    uow.review_runs.add(replace(run, status=ReviewRunStatus.CANCELLED))
    vcs = RecordingVcsGateway()

    with caplog.at_level(logging.INFO, logger="app.application.publish_review"):
        _publish_first_run(uow, run, vcs)

    assert vcs.publications == []
    assert vcs.checks == []
    assert any(str(RUN_ID) in r.getMessage() for r in caplog.records)


class _RetargetDuringGateVcs(RecordingVcsGateway):
    """GitHub, на чей первый POST check run гонкой приходится смена целевой ветки:
    приём отменяет прогон и коммитит новый - ровно то, что делает `handle_webhook`."""

    def __init__(self, uow: FakeUnitOfWork, new_status: ReviewRunStatus) -> None:
        super().__init__()
        self._uow = uow
        self._new_status = new_status

    def set_merge_gate_check(self, *args: Any) -> None:
        super().set_merge_gate_check(*args)
        if len(self.checks) > 1:
            return
        stale = self._uow.review_runs.get(RUN_ID)
        assert stale is not None
        self._uow.review_runs.add(replace(stale, status=ReviewRunStatus.CANCELLED))
        moment = NOW + timedelta(minutes=1)
        self._uow.review_runs.add(
            replace(stale, id=UUID(int=77), status=self._new_status, created_at=moment, updated_at=moment)
        )


def test_retarget_racing_the_gate_of_a_stale_run_ends_in_failure() -> None:
    """Блокер #225: прогон прочитал статус до отмены и выставил `success` по старому
    диффу уже после блокировки приёма - он перечитывает прогон и возвращает блокировку."""
    uow = FakeUnitOfWork()
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    vcs = _RetargetDuringGateVcs(uow, ReviewRunStatus.QUEUED)

    _publish_first_run(uow, run, vcs)

    assert [c.conclusion for c in vcs.checks] == ["success", "failure"]
    assert vcs.checks[-1] == build_retarget_check()


class _FailingCorrectionVcs(_RetargetDuringGateVcs):
    """Поправку gate (POST после первого) GitHub отвергает `failures` раз подряд."""

    def __init__(self, uow: FakeUnitOfWork, failures: int) -> None:
        super().__init__(uow, ReviewRunStatus.QUEUED)
        self._failures = failures
        self.correction_attempts = 0

    def set_merge_gate_check(self, *args: Any) -> None:
        if self.checks:
            self.correction_attempts += 1
            if self.correction_attempts <= self._failures:
                raise VcsTransientError("GitHub API: HTTP 502")
        super().set_merge_gate_check(*args)


def test_a_failed_gate_correction_is_retried() -> None:
    """Ревью #225: поправку отправляет только тот, кто увидел смену прогона, - после
    одного сбоя на head остался бы `success` по старому диффу."""
    uow = FakeUnitOfWork()
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    vcs = _FailingCorrectionVcs(uow, failures=SETTLE_ATTEMPTS - 1)

    _publish_first_run(uow, run, vcs)

    assert vcs.correction_attempts == SETTLE_ATTEMPTS
    assert vcs.checks[-1] == build_retarget_check()


class _CompletedDuringFailureVcs(_FailingCorrectionVcs):
    """Пока поправка не дошла, новый прогон успел завершиться."""

    def set_merge_gate_check(self, *args: Any) -> None:
        try:
            super().set_merge_gate_check(*args)
        except VcsError:
            newer = self._uow.review_runs.get(UUID(int=77))
            assert newer is not None
            self._uow.review_runs.add(replace(newer, status=ReviewRunStatus.COMPLETED))
            raise


def test_a_retried_gate_correction_is_rebuilt_for_the_current_run() -> None:
    """Повтор не отправляет check, собранный до сбоя: за время сбоя новый прогон
    завершился, и блокировка смены ветки перекрыла бы его итог."""
    uow = FakeUnitOfWork()
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    vcs = _CompletedDuringFailureVcs(uow, failures=1)

    _publish_first_run(uow, run, vcs)

    assert [c.conclusion for c in vcs.checks] == ["success", "success"]
    assert vcs.checks[-1] != build_retarget_check()


def test_settling_on_a_cancelled_latest_run_posts_once() -> None:
    """Последний прогон на head сам отменён: блокировка выставляется один раз, и
    цикл поправки не крутится на нём."""
    uow = FakeUnitOfWork()
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    moment = NOW + timedelta(minutes=1)
    uow.review_runs.add(
        replace(run, id=UUID(int=77), status=ReviewRunStatus.CANCELLED, created_at=moment, updated_at=moment)
    )
    vcs = RecordingVcsGateway()

    settle_merge_gate(
        uow,
        vcs,
        merge_request_id=MR_ID,
        repo_full_name=JOB.repository_full_name,
        head_sha=JOB.head_sha,
        installation_id=JOB.installation_id,
        run_id=run.id,
    )

    assert vcs.checks == [build_retarget_check()]


def test_settling_on_a_failed_latest_run_keeps_the_failure() -> None:
    """Ревью #225: после ретаргета новый прогон быстро упал и выставил «Ревью не
    удалось». Поправка от старого прогона не подменяет его блокировкой «идёт
    повторное ревью» - повторного ревью не будет."""
    uow = FakeUnitOfWork()
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    uow.review_runs.add(replace(run, status=ReviewRunStatus.CANCELLED))
    moment = NOW + timedelta(minutes=1)
    uow.review_runs.add(
        replace(run, id=UUID(int=77), status=ReviewRunStatus.FAILED, created_at=moment, updated_at=moment)
    )
    vcs = RecordingVcsGateway()

    settle_merge_gate(
        uow,
        vcs,
        merge_request_id=MR_ID,
        repo_full_name=JOB.repository_full_name,
        head_sha=JOB.head_sha,
        installation_id=JOB.installation_id,
        run_id=run.id,
    )

    assert vcs.checks == [build_failed_review_check()]


class _CompletedDuringCorrectionVcs(_RetargetDuringGateVcs):
    """Поправка-блокировка дошла уже после того, как новый прогон завершился и
    выставил свой gate (ревью #225): POST блокировки обгоняет завершение."""

    def set_merge_gate_check(self, *args: Any) -> None:
        super().set_merge_gate_check(*args)
        if len(self.checks) != 2:
            return
        newer = self._uow.review_runs.get(UUID(int=77))
        assert newer is not None
        self._uow.review_runs.add(replace(newer, status=ReviewRunStatus.COMPLETED))


def test_a_review_completed_behind_the_correction_gets_its_gate_back() -> None:
    """Блокировка легла поверх `success` завершившегося нового прогона: поправка
    видит, что тот уже `completed`, и выставляет его итог, а не оставляет блокировку."""
    uow = FakeUnitOfWork()
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    vcs = _CompletedDuringCorrectionVcs(uow, ReviewRunStatus.QUEUED)

    _publish_first_run(uow, run, vcs)

    assert [c.conclusion for c in vcs.checks] == ["success", "failure", "success"]
    assert vcs.checks[-1] != build_retarget_check()


def test_gate_correction_gives_up_after_its_attempts() -> None:
    """GitHub отвергает все попытки: публикация не падает, сбой в логе, а head
    закроет `failure` упавшего нового прогона (`run_review`)."""
    uow = FakeUnitOfWork()
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    vcs = _FailingCorrectionVcs(uow, failures=SETTLE_ATTEMPTS)

    _publish_first_run(uow, run, vcs)

    assert vcs.correction_attempts == SETTLE_ATTEMPTS
    assert [c.conclusion for c in vcs.checks] == ["success"]


def test_gate_of_a_stale_run_yields_to_a_completed_review_of_the_new_target() -> None:
    """Новый прогон успел завершиться: gate выставляется по нему, а не блокировкой."""
    uow = FakeUnitOfWork()
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    vcs = _RetargetDuringGateVcs(uow, ReviewRunStatus.COMPLETED)

    _publish_first_run(uow, run, vcs)

    assert [c.conclusion for c in vcs.checks] == ["success", "success"]
    assert vcs.checks[-1] != build_retarget_check()


def test_orphan_adopted_by_a_concurrent_run_does_not_fail_publishing() -> None:
    """Два прогона одновременно подобрали одно ревью: проигравший видит отказ
    уникального индекса, откатывает подбор и публикует своё ревью."""
    uow = FakeUnitOfWork()
    _seed_orphan(uow)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    uow.commit_errors = [ActiveRunConflict("uq_published_comments_provider_comment_id")]
    vcs = RecordingVcsGateway(stored_reviews={ORPHAN_RUN_ID})

    _publish(uow, vcs)

    assert len(vcs.publications) == 1
    assert uow.published_comments.list_for_run(ORPHAN_RUN_ID) == []


def _publish_another_run(uow: FakeUnitOfWork, at: datetime) -> None:
    """Другой прогон PR опубликовал ревью в `at` - запись в `published_comments`."""
    orphan = uow.review_runs.get(ORPHAN_RUN_ID)
    assert orphan is not None
    published_run = UUID(int=55)
    uow.review_runs.add(
        replace(orphan, id=published_run, status=ReviewRunStatus.COMPLETED, failed_stage=None, created_at=at)
    )
    uow.published_comments.add(
        PublishedComment(
            id=UUID(int=56),
            review_run_id=published_run,
            finding_id=None,
            provider_comment_id="later-summary",
            kind=CommentKind.SUMMARY,
            published_at=at,
            created_at=at,
            updated_at=at,
        )
    )


def test_orphan_failed_before_a_later_publication_is_not_looked_up_again() -> None:
    """Прогон упал раньше, чем другой прогон опубликовал ревью: тот его уже
    проверял - GitHub ради него не листаем."""
    uow = FakeUnitOfWork()
    _seed_orphan(uow)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    orphan = uow.review_runs.get(ORPHAN_RUN_ID)
    assert orphan is not None
    _publish_another_run(uow, orphan.last_progress_at + timedelta(minutes=1))
    vcs = RecordingVcsGateway(stored_reviews={ORPHAN_RUN_ID})

    _publish(uow, vcs)

    assert vcs.found_review_requests == []


def test_orphan_failed_by_the_sweep_after_a_later_publication_is_looked_up() -> None:
    """Ревью #225: A сделал POST и умер на `publishing`, B его не видел (A ещё не
    `failed`) и опубликовался, потом выметатель перевёл A в `failed`. A создан
    раньше B, но упал позже - его ревью подбирается, а не дублируется."""
    uow = FakeUnitOfWork()
    _seed_orphan(uow)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    orphan = uow.review_runs.get(ORPHAN_RUN_ID)
    assert orphan is not None
    published_at = orphan.created_at + timedelta(minutes=1)
    _publish_another_run(uow, published_at)
    uow.review_runs.add(replace(orphan, last_progress_at=published_at + timedelta(minutes=1)))
    vcs = RecordingVcsGateway(stored_reviews={ORPHAN_RUN_ID})

    _publish(uow, vcs)

    assert vcs.found_review_requests == [ORPHAN_RUN_ID]
