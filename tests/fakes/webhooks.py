"""Двойники приёма вебхуков: VCS и очередь, builder'ы и записанные payload'ы.

Тестам приёма нужна база без сети: событие собирается из fixture'ов, VCS и
очередь хранят сущности как есть и записывают вызовы, поэтому тесты сверяют
исход с точными записями и задачей.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    PullRequestReviewPublication,
    ReviewThreadState,
)
from app.domain.entities import PRMetadata, Repository, ReviewJob
from app.domain.enums import MergeRequestState, Provider
from app.domain.vcs_errors import VcsError, VcsFileTooLargeError

FIXTURES = Path(__file__).parent.parent / "fixtures"
NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)

REGISTERED_PROVIDER_ID = "923478362"
INSTALLATION_ID = 512804923
REPO_FULL_NAME = "larchanka-training/dmc-268-api-t1"
DIFF = (
    "diff --git a/app/main.py b/app/main.py\n"
    "--- a/app/main.py\n+++ b/app/main.py\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)


def webhook_payload(action: str = "opened", *, base_ref: str | None = None) -> dict[str, Any]:
    """Записанный payload GitHub; для прочих действий — тело `opened` с заменой.

    `base_ref` - целевая ветка в payload'е, если не та, что в записи."""
    name = (
        "github_webhook_synchronize.json"
        if action == "synchronize"
        else "github_webhook_opened.json"
    )
    raw: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    raw["action"] = action
    if action == "edited":
        # `edited` в тестах - смена целевой ветки: иное `edited` ревью не запускает.
        raw["changes"] = {"base": {"ref": {"from": "evil-staging"}, "sha": {"from": "0" * 40}}}
    if base_ref is not None:
        raw["pull_request"]["base"]["ref"] = base_ref
    return raw


def pr_metadata(
    base_sha: str = "fedcba0987654321fedcba0987654321fedcba09", target_branch: str = "main"
) -> PRMetadata:
    """Свежие метаданные VCS.

    Заголовок, автор и ветки нарочно отличаются от payload'а вебхука: так
    тест видит, что в `MergeRequest` ложится версия из метаданных, а не
    устаревшая копия из доставленного тела.
    """
    return PRMetadata(
        number=6,
        head_sha="a1b2c3d4e5f6789012345678abcdef0123456789",
        base_sha=base_sha,
        title="feat: метаданные из VCS свежее payload'а",
        description="",
        author="grinv",
        source_branch="feat/webhook-intake-rebased",
        target_branch=target_branch,
        state=MergeRequestState.OPEN,
    )


def a_repository() -> Repository:
    return Repository(
        id=uuid4(),
        provider=Provider.GITHUB,
        provider_id=REGISTERED_PROVIDER_ID,
        full_name=REPO_FULL_NAME,
        default_branch="develop",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )


@dataclass
class FakeVcs:
    diff: str = DIFF
    meta: PRMetadata = field(default_factory=pr_metadata)
    error: VcsError | None = None
    # Содержимое файлов по (путь, sha); отсутствие ключа — файла на коммите нет.
    files: dict[tuple[str, str], str] = field(default_factory=dict)
    # Сбой довыборки конкретного файла — не задевает дифф и метаданные.
    file_errors: dict[str, VcsError] = field(default_factory=dict)
    file_limits: list[int] = field(default_factory=list)
    # Сообщения коммитов PR от нового к старому и сбой только их получения.
    commit_messages: tuple[str, ...] = ()
    commits_error: VcsError | None = None
    # Сбой только метаданных PR - дифф при этом достаётся.
    metadata_error: VcsError | None = None
    # Сбой только check run merge gate; выставленные check run - (head_sha, check).
    check_error: VcsError | None = None
    checks: list[tuple[str, MergeGateCheck]] = field(default_factory=list)
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def fetch_diff(
        self, repo_full_name: str, base_sha: str, head_sha: str, installation_id: int, *, max_bytes: int
    ) -> str:
        self.calls.append(("diff", repo_full_name, base_sha, head_sha, installation_id))
        if self.error is not None:
            raise self.error
        if len(self.diff.encode("utf-8")) > max_bytes:
            raise VcsFileTooLargeError(f"дифф: больше {max_bytes} байт")
        return self.diff

    def fetch_pr_metadata(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> PRMetadata:
        self.calls.append(("metadata", repo_full_name, pr_number, installation_id))
        error = self.metadata_error or self.error
        if error is not None:
            raise error
        return self.meta

    def fetch_pr_commit_messages(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> tuple[str, ...]:
        self.calls.append(("commits", repo_full_name, pr_number, installation_id))
        error = self.commits_error or self.error
        if error is not None:
            raise error
        return self.commit_messages

    def fetch_file(
        self, repo_full_name: str, path: str, sha: str, installation_id: int, *, max_bytes: int
    ) -> str | None:
        self.calls.append(("file", repo_full_name, path, sha, installation_id))
        self.file_limits.append(max_bytes)
        if path in self.file_errors:
            raise self.file_errors[path]
        content = self.files.get((path, sha))
        if content is not None and len(content.encode("utf-8")) > max_bytes:
            raise VcsFileTooLargeError(f"{path}: больше {max_bytes} байт")
        return content

    def publish_pull_request_review(
        self,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        installation_id: int,
        summary_body: str,
        inline_comments: tuple[InlineCommentDraft, ...],
        *,
        review_run_id: UUID,
    ) -> PullRequestReviewPublication:
        self.calls.append(
            ("publish_review", repo_full_name, pr_number, head_sha, installation_id)
        )
        if self.error is not None:
            raise self.error
        return PullRequestReviewPublication(summary_provider_comment_id=None, inline_comments=())

    def find_published_review(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        inline_comments: tuple[InlineCommentDraft, ...],
        *,
        review_run_id: UUID,
    ) -> PullRequestReviewPublication | None:
        self.calls.append(("find_review", review_run_id))
        if self.error is not None:
            raise self.error
        return None

    def fetch_review_threads_state(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_ids: frozenset[str],
    ) -> dict[str, ReviewThreadState]:
        self.calls.append(("thread_state", provider_comment_ids))
        if self.error is not None:
            raise self.error
        return {cid: ReviewThreadState(resolved=False, line=None) for cid in provider_comment_ids}

    def set_merge_gate_check(
        self,
        repo_full_name: str,
        head_sha: str,
        installation_id: int,
        check: MergeGateCheck,
    ) -> None:
        self.calls.append(("check", repo_full_name, head_sha, check.conclusion))
        if self.error is not None:
            raise self.error
        if self.check_error is not None:
            raise self.check_error
        self.checks.append((head_sha, check))


@dataclass
class FakeQueue:
    jobs: list[ReviewJob] = field(default_factory=list)

    def enqueue(self, job: ReviewJob) -> None:
        self.jobs.append(job)
