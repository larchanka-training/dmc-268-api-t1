"""Записывающий `VcsGateway` для тестов публикации."""

from dataclasses import dataclass, field
from uuid import UUID

from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    PublishedInlineComment,
    PullRequestReviewPublication,
    ReviewThreadState,
)
from app.domain.entities import PRMetadata
from app.domain.enums import MergeRequestState
from app.domain.vcs_errors import VcsFileTooLargeError

# Как у `FakeVcs`: первая строка `app/main.py` в диффе для сборки контекста.
_PIPELINE_DIFF = (
    "diff --git a/app/main.py b/app/main.py\n"
    "--- a/app/main.py\n+++ b/app/main.py\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)


@dataclass
class RecordingVcsGateway:
    diff: str = _PIPELINE_DIFF
    # Содержимое файлов по (путь, sha); отсутствие ключа - файла на коммите нет.
    files: dict[tuple[str, str], str] = field(default_factory=dict)
    checks: list[MergeGateCheck] = field(default_factory=list)
    publications: list[tuple[str, tuple[InlineCommentDraft, ...]]] = field(
        default_factory=list
    )
    # Контекст PR для сборки пакета: описание и сообщения коммитов (новые первыми).
    pr_title: str = "feat: изменение"
    pr_description: str = ""
    commit_messages: tuple[str, ...] = ()
    # Треды на хостинге по корневому комментарию; не заданный - открыт и устарел
    # (`line=None`): с находками прогона он не совпадает.
    thread_states: dict[str, ReviewThreadState] = field(default_factory=dict)
    thread_state_requests: list[frozenset[str]] = field(default_factory=list)
    # Сбой сверки тредов: `fetch_review_threads_state` бросает это исключение.
    threads_error: Exception | None = None
    # Ревью опубликовано, а id inline-комментариев не получены (`GET …/comments` упал).
    inline_ids_lost: bool = False
    # Сбой самого check run merge gate.
    check_error: Exception | None = None
    # Прогоны, чьим маркером помечено каждое опубликованное ревью.
    review_run_ids: list[UUID] = field(default_factory=list)
    # Прогоны, чьё ревью с маркером уже есть на хостинге (`find_published_review`):
    # сводка - `orphan-summary`, inline - на каждой строке черновика.
    stored_reviews: set[UUID] = field(default_factory=set)
    found_review_requests: list[UUID] = field(default_factory=list)
    find_review_error: Exception | None = None

    def fetch_diff(
        self, repo_full_name: str, base_sha: str, head_sha: str, installation_id: int, *, max_bytes: int
    ) -> str:
        if len(self.diff.encode("utf-8")) > max_bytes:
            raise VcsFileTooLargeError(f"дифф: больше {max_bytes} байт")
        return self.diff

    def fetch_pr_metadata(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> PRMetadata:
        return PRMetadata(
            number=pr_number,
            head_sha="a" * 40,
            base_sha="b" * 40,
            title=self.pr_title,
            description=self.pr_description,
            author="author",
            source_branch="feat",
            target_branch="develop",
            state=MergeRequestState.OPEN,
        )

    def fetch_pr_commit_messages(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> tuple[str, ...]:
        return self.commit_messages

    def fetch_file(
        self, repo_full_name: str, path: str, sha: str, installation_id: int, *, max_bytes: int
    ) -> str | None:
        return self.files.get((path, sha))

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
        self.publications.append((summary_body, inline_comments))
        self.review_run_ids.append(review_run_id)
        if self.inline_ids_lost:
            return PullRequestReviewPublication(
                summary_provider_comment_id="summary-1",
                inline_comments=tuple(None for _ in inline_comments),
            )
        published = tuple(
            PublishedInlineComment(
                file_path=item.file_path,
                side=item.side,
                line=item.line,
                provider_comment_id=f"inline-{index}-{item.file_path}-{item.line}",
            )
            for index, item in enumerate(inline_comments)
        )
        return PullRequestReviewPublication(
            summary_provider_comment_id="summary-1",
            inline_comments=published,
        )

    def find_published_review(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        inline_comments: tuple[InlineCommentDraft, ...],
        *,
        review_run_id: UUID,
    ) -> PullRequestReviewPublication | None:
        self.found_review_requests.append(review_run_id)
        if self.find_review_error is not None:
            raise self.find_review_error
        if review_run_id not in self.stored_reviews:
            return None
        return PullRequestReviewPublication(
            summary_provider_comment_id="orphan-summary",
            inline_comments=tuple(
                PublishedInlineComment(
                    file_path=item.file_path,
                    side=item.side,
                    line=item.line,
                    provider_comment_id=f"orphan-inline-{item.file_path}-{item.line}",
                )
                for item in inline_comments
            ),
        )

    def fetch_review_threads_state(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_ids: frozenset[str],
    ) -> dict[str, ReviewThreadState]:
        self.thread_state_requests.append(provider_comment_ids)
        if self.threads_error is not None:
            raise self.threads_error
        return {
            cid: self.thread_states.get(cid, ReviewThreadState(resolved=False, line=None))
            for cid in provider_comment_ids
        }

    def set_merge_gate_check(
        self,
        repo_full_name: str,
        head_sha: str,
        installation_id: int,
        check: MergeGateCheck,
    ) -> None:
        if self.check_error is not None:
            raise self.check_error
        self.checks.append(check)
