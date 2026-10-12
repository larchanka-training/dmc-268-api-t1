"""Публикация ревью и check run через GitHub REST и GraphQL."""

from typing import Any

from app.application.ports.vcs_gateway import CHECK_RUN_NAME
from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    PublishedInlineComment,
    PullRequestReviewPublication,
)
from app.domain.enums import DiffSide
from app.infrastructure.vcs.errors import VcsError


def _github_side(side: DiffSide) -> str:
    return "RIGHT" if side is DiffSide.NEW else "LEFT"


def _line_for_side(draft: InlineCommentDraft) -> int:
    return draft.line


class GitHubReviewClient:
    """Методы публикации; вызываются из `GitHubVcsGateway`."""

    def __init__(self, gateway: Any) -> None:
        self._gw = gateway

    def publish_pull_request_review(
        self,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        installation_id: int,
        summary_body: str,
        inline_comments: tuple[InlineCommentDraft, ...],
    ) -> PullRequestReviewPublication:
        comments_payload = [
            {
                "path": item.file_path,
                "body": item.body,
                "line": _line_for_side(item),
                "side": _github_side(item.side),
            }
            for item in inline_comments
        ]
        payload: dict[str, Any] = {
            "commit_id": head_sha,
            "body": summary_body,
            "event": "COMMENT",
        }
        if comments_payload:
            payload["comments"] = comments_payload
        response = self._gw._post_json(
            f"/repos/{repo_full_name}/pulls/{pr_number}/reviews",
            installation_id,
            payload,
        )
        summary_id = None
        if response.get("body"):
            summary_id = str(response["id"])
        published: list[PublishedInlineComment] = []
        for raw in response.get("comments") or []:
            path = raw["path"]
            side = DiffSide.NEW if raw.get("side", "RIGHT") == "RIGHT" else DiffSide.OLD
            line = int(raw["line"])
            published.append(
                PublishedInlineComment(
                    file_path=path,
                    side=side,
                    line=line,
                    provider_comment_id=str(raw["id"]),
                )
            )
        return PullRequestReviewPublication(
            summary_provider_comment_id=summary_id,
            inline_comments=tuple(published),
        )

    def reply_to_review_comment(
        self,
        repo_full_name: str,
        installation_id: int,
        provider_comment_id: str,
        body: str,
    ) -> str:
        response = self._gw._post_json(
            f"/repos/{repo_full_name}/pulls/comments/{provider_comment_id}/replies",
            installation_id,
            {"body": body},
        )
        return str(response["id"])

    def resolve_review_thread_for_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
    ) -> None:
        owner, name = repo_full_name.split("/", 1)
        thread_id = self._find_thread_id(owner, name, pr_number, installation_id, provider_comment_id)
        mutation = """
        mutation($threadId: ID!) {
          resolveReviewThread(input: {threadId: $threadId}) {
            thread { isResolved }
          }
        }
        """
        data = self._gw._post_graphql(
            installation_id,
            mutation,
            {"threadId": thread_id},
        )
        if not data.get("resolveReviewThread", {}).get("thread", {}).get("isResolved"):
            raise VcsError(f"не удалось закрыть тред для комментария {provider_comment_id}")

    def set_merge_gate_check(
        self,
        repo_full_name: str,
        head_sha: str,
        installation_id: int,
        check: MergeGateCheck,
    ) -> None:
        self._gw._post_json(
            f"/repos/{repo_full_name}/check-runs",
            installation_id,
            {
                "name": CHECK_RUN_NAME,
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": check.conclusion,
                "output": {"title": check.title, "summary": check.summary},
            },
        )

    def _find_thread_id(
        self,
        owner: str,
        name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
    ) -> str:
        query = """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              reviewThreads(first: 100) {
                nodes {
                  id
                  comments(first: 50) {
                    nodes { databaseId }
                  }
                }
              }
            }
          }
        }
        """
        data = self._gw._post_graphql(
            installation_id,
            query,
            {"owner": owner, "name": name, "number": pr_number},
        )
        target = int(provider_comment_id)
        threads = (
            data.get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )
        for thread in threads:
            for node in thread.get("comments", {}).get("nodes", []):
                if node.get("databaseId") == target:
                    return thread["id"]
        raise VcsError(f"тред для комментария {provider_comment_id} не найден")
