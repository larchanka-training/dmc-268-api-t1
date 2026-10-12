"""Записывающий `VcsGateway` для тестов публикации."""

from dataclasses import dataclass, field

from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    PublishedInlineComment,
    PullRequestReviewPublication,
)
from app.domain.entities import PRMetadata


@dataclass
class RecordingVcsGateway:
    checks: list[MergeGateCheck] = field(default_factory=list)
    replies: list[tuple[str, str]] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    publications: list[tuple[str, tuple[InlineCommentDraft, ...]]] = field(
        default_factory=list
    )

    def fetch_diff(
        self, repo_full_name: str, base_sha: str, head_sha: str, installation_id: int
    ) -> str:
        return ""

    def fetch_pr_metadata(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> PRMetadata:
        raise NotImplementedError

    def publish_pull_request_review(
        self,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        installation_id: int,
        summary_body: str,
        inline_comments: tuple[InlineCommentDraft, ...],
    ) -> PullRequestReviewPublication:
        self.publications.append((summary_body, inline_comments))
        published = tuple(
            PublishedInlineComment(
                file_path=item.file_path,
                side=item.side,
                line=item.line,
                provider_comment_id=f"inline-{item.file_path}-{item.line}",
            )
            for item in inline_comments
        )
        return PullRequestReviewPublication(
            summary_provider_comment_id="summary-1",
            inline_comments=published,
        )

    def reply_to_review_comment(
        self,
        repo_full_name: str,
        installation_id: int,
        provider_comment_id: str,
        body: str,
    ) -> str:
        self.replies.append((provider_comment_id, body))
        return f"reply-to-{provider_comment_id}"

    def resolve_review_thread_for_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
    ) -> None:
        self.resolved.append(provider_comment_id)

    def set_merge_gate_check(
        self,
        repo_full_name: str,
        head_sha: str,
        installation_id: int,
        check: MergeGateCheck,
    ) -> None:
        self.checks.append(check)
