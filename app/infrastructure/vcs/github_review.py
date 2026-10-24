"""Публикация ревью и check run через GitHub REST и GraphQL."""

from typing import Any

from app.application.ports.vcs_gateway import CHECK_RUN_NAME
from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    PublishedInlineComment,
    PullRequestReviewPublication,
    ReviewThreadState,
)
from app.domain.enums import DiffSide
from app.infrastructure.vcs.errors import VcsError


def _github_side(side: DiffSide) -> str:
    return "RIGHT" if side is DiffSide.NEW else "LEFT"


def _raw_comment_line(raw: dict[str, Any]) -> int | None:
    line = raw.get("line")
    if line is None:
        line = raw.get("original_line")
    if line is None:
        return None
    try:
        return int(line)
    except (TypeError, ValueError):
        return None


def _thread_current_line(thread: dict[str, Any]) -> int | None:
    """Строка треда в текущем диффе PR; у устаревшего треда её нет.

    У outdated-треда GitHub отдаёт `line: null` и `isOutdated: true` (проверено
    на живом PR); `originalLine` - строка на коммите комментария, к нынешнему
    коду она не относится и здесь не берётся.
    """
    if thread.get("isOutdated") is not False:
        return None
    line = thread.get("line")
    return line if isinstance(line, int) else None


def _map_fetched_inline_comments(
    drafts: tuple[InlineCommentDraft, ...],
    raw_comments: list[dict[str, Any]],
) -> tuple[PublishedInlineComment | None, ...]:
    published: list[PublishedInlineComment | None] = []
    used_provider_ids: set[str] = set()
    for draft in drafts:
        want_side = _github_side(draft.side)
        want_line = draft.line
        match = next(
            (
                raw
                for raw in raw_comments
                if str(raw.get("id")) not in used_provider_ids
                and raw.get("path") == draft.file_path
                and _raw_comment_line(raw) == want_line
                and raw.get("side", "RIGHT") == want_side
            ),
            None,
        )
        if match is None:
            published.append(None)
            continue
        side = DiffSide.NEW if match.get("side", "RIGHT") == "RIGHT" else DiffSide.OLD
        provider_id = str(match["id"])
        used_provider_ids.add(provider_id)
        published.append(
            PublishedInlineComment(
                file_path=draft.file_path,
                side=side,
                line=want_line,
                provider_comment_id=provider_id,
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
                "line": item.line,
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
        published: tuple[PublishedInlineComment | None, ...] = ()
        if inline_comments:
            review_id = response["id"]
            raw_comments = self._gw._get_json_list_paginated(
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

    def fetch_review_threads_state(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_ids: frozenset[str],
    ) -> dict[str, ReviewThreadState]:
        if not provider_comment_ids:
            return {}
        owner, name = repo_full_name.split("/", 1)
        targets = {int(cid) for cid in provider_comment_ids}
        threads = self._load_review_threads(owner, name, pr_number, installation_id)
        result: dict[str, ReviewThreadState] = {}
        for thread in threads:
            is_resolved = thread.get("isResolved")
            if not isinstance(is_resolved, bool):
                continue
            state = ReviewThreadState(resolved=is_resolved, line=_thread_current_line(thread))
            for node in thread.get("comments", {}).get("nodes", []):
                database_id = node.get("databaseId")
                if database_id in targets:
                    result[str(database_id)] = state
        return result

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

    def _load_review_threads(
        self,
        owner: str,
        name: str,
        pr_number: int,
        installation_id: int,
    ) -> list[dict[str, Any]]:
        query = """
        query($owner: String!, $name: String!, $number: Int!, $after: String) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              reviewThreads(first: 100, after: $after) {
                pageInfo { hasNextPage endCursor }
                nodes {
                  id
                  isResolved
                  isOutdated
                  line
                  comments(first: 50) {
                    nodes { databaseId }
                  }
                }
              }
            }
          }
        }
        """
        threads: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            data = self._gw._post_graphql(
                installation_id,
                query,
                {"owner": owner, "name": name, "number": pr_number, "after": cursor},
            )
            block = (
                data.get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
            )
            nodes = block.get("nodes", [])
            threads.extend(node for node in nodes if isinstance(node, dict))
            page_info = block.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                return threads
            next_cursor = page_info.get("endCursor")
            if not isinstance(next_cursor, str):
                return threads
            cursor = next_cursor

    def _thread_for_comment(
        self,
        owner: str,
        name: str,
        pr_number: int,
        installation_id: int,
        provider_comment_id: str,
    ) -> tuple[bool, str] | None:
        target = int(provider_comment_id)
        for thread in self._load_review_threads(owner, name, pr_number, installation_id):
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
