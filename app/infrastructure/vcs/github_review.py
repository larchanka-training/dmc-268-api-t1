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


def _map_fetched_inline_comments(
    drafts: tuple[InlineCommentDraft, ...],
    raw_comments: list[dict[str, Any]],
) -> tuple[PublishedInlineComment, ...]:
    published: list[PublishedInlineComment] = []
    for draft in drafts:
        want_side = _github_side(draft.side)
        want_line = _line_for_side(draft)
        match = next(
            (
                raw
                for raw in raw_comments
                if raw.get("path") == draft.file_path
                and int(raw.get("line", -1)) == want_line
                and raw.get("side", "RIGHT") == want_side
            ),
            None,
        )
        if match is None:
            continue
        side = DiffSide.NEW if match.get("side", "RIGHT") == "RIGHT" else DiffSide.OLD
        published.append(
            PublishedInlineComment(
                file_path=draft.file_path,
                side=side,
                line=want_line,
                provider_comment_id=str(match["id"]),
            )
        )
    return tuple(published)


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
        published: tuple[PublishedInlineComment, ...] = ()
        if inline_comments:
            review_id = response["id"]
            raw_comments = self._gw._get_json_list(
                f"/repos/{repo_full_name}/pulls/{pr_number}/reviews/{review_id}/comments",
                installation_id,
            )
            published = _map_fetched_inline_comments(inline_comments, raw_comments)
        return PullRequestReviewPublication(
            summary_provider_comment_id=summary_id,
            inline_comments=published,
        )

    def reply_to_review_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
        body: str,
    ) -> str:
        response = self._gw._post_json(
            f"/repos/{repo_full_name}/pulls/{pr_number}/comments/{provider_comment_id}/replies",
            installation_id,
            {"body": body},
        )
        return str(response["id"])

    def is_review_thread_resolved(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
    ) -> bool | None:
        owner, name = repo_full_name.split("/", 1)
        state = self._thread_for_comment(
            owner, name, pr_number, installation_id, provider_comment_id
        )
        if state is None:
            return None
        return state[0]

    def resolve_review_thread_for_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
    ) -> None:
        owner, name = repo_full_name.split("/", 1)
        state = self._thread_for_comment(
            owner, name, pr_number, installation_id, provider_comment_id
        )
        if state is None:
            raise VcsError(f"тред для комментария {provider_comment_id} не найден")
        thread_id = state[1]
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

    def _thread_for_comment(
        self,
        owner: str,
        name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
    ) -> tuple[bool, str] | None:
        query = """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              reviewThreads(first: 100) {
                nodes {
                  id
                  isResolved
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
                    thread_id = thread["id"]
                    if not isinstance(thread_id, str):
                        raise VcsError("GraphQL вернул thread id не-строкой")
                    is_resolved = thread.get("isResolved")
                    if not isinstance(is_resolved, bool):
                        raise VcsError("GraphQL вернул isResolved не bool")
                    return is_resolved, thread_id
        return None
