"""Публикация ревью и check run через GitHub REST и GraphQL."""

import logging
from typing import Any
from uuid import UUID

from app.application.ports.vcs_gateway import CHECK_RUN_NAME
from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    PublishedInlineComment,
    PullRequestReviewPublication,
    ReviewThreadState,
)
from app.domain.enums import DiffSide
from app.infrastructure.vcs.errors import (
    VcsError,
)

logger = logging.getLogger(__name__)


def review_run_marker(review_run_id: UUID) -> str:
    """Скрытый маркер прогона в теле ревью: HTML-комментарий GitHub не показывает,
    а `GET …/reviews` отдаёт в `body` как есть."""
    return f"<!-- dmc-268-review-run:{review_run_id} -->"


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
        *,
        review_run_id: UUID,
    ) -> PullRequestReviewPublication:
        """Одно ревью с маркером прогона в теле; повтор `POST` - только после поиска.

        Список inline-комментариев не получен - ревью всё равно опубликовано:
        id сводки возвращается, inline - `None` (не сопоставлены).
        """
        marker = review_run_marker(review_run_id)
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
            "body": f"{summary_body}\n\n{marker}",
            "event": "COMMENT",
        }
        if comments_payload:
            payload["comments"] = comments_payload
        reviews_url = f"/repos/{repo_full_name}/pulls/{pr_number}/reviews"
        self._learn_bot_login()
        response = self._gw._post_json(
            reviews_url,
            installation_id,
            payload,
            # Ответа на POST нет - после паузы ищем своё ревью и шлём снова, только если его нет.
            already_done=lambda: self._review_found_by_marker(
                reviews_url, installation_id, marker, review_run_id
            ),
        )
        try:
            review_id = str(response["id"])
        except (KeyError, TypeError) as error:
            raise VcsError(f"GitHub API вернул ревью без id на POST {reviews_url}") from error
        published: tuple[PublishedInlineComment | None, ...] = ()
        if inline_comments:
            try:
                published = self._inline_comments_of(
                    reviews_url, review_id, installation_id, inline_comments
                )
            except VcsError:
                logger.warning(
                    "прогон %s: ревью %s опубликовано, а id inline-комментариев не получены",
                    review_run_id,
                    review_id,
                    exc_info=True,
                )
                published = tuple(None for _ in inline_comments)
        return PullRequestReviewPublication(
            summary_provider_comment_id=review_id,
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
        """Ревью прогона, которое бот уже опубликовал: поиск по маркеру и его inline.

        В отличие от публикации, сбой чтения inline - `VcsError`: вызывающий
        записывает найденное и не должен принять потерю id за отсутствие.
        """
        reviews_url = f"/repos/{repo_full_name}/pulls/{pr_number}/reviews"
        review_id = self._find_review_by_marker(
            reviews_url, installation_id, review_run_marker(review_run_id)
        )
        if review_id is None:
            return None
        published: tuple[PublishedInlineComment | None, ...] = ()
        if inline_comments:
            published = self._inline_comments_of(
                reviews_url, review_id, installation_id, inline_comments
            )
        return PullRequestReviewPublication(
            summary_provider_comment_id=review_id, inline_comments=published
        )

    def _inline_comments_of(
        self,
        reviews_url: str,
        review_id: str,
        installation_id: int,
        inline_comments: tuple[InlineCommentDraft, ...],
    ) -> tuple[PublishedInlineComment | None, ...]:
        raw_comments = self._gw._get_json_list_paginated(
            f"{reviews_url}/{review_id}/comments", installation_id
        )
        return _map_fetched_inline_comments(inline_comments, raw_comments)

    def _learn_bot_login(self) -> None:
        """Логин бота - до `POST`, best-effort: он нужен поиску по маркеру, а поиск
        идёт, когда GitHub уже сбоит, и `GET /app` тогда упал бы вместе с ним.
        Логин кэшируется на процесс; не получили сейчас - поиск спросит ещё раз."""
        try:
            self._gw.bot_login()
        except VcsError:
            logger.warning("логин бота GitHub App не получен до публикации ревью", exc_info=True)

    def _review_found_by_marker(
        self, reviews_url: str, installation_id: int, marker: str, review_run_id: UUID
    ) -> dict[str, Any] | None:
        """Ответ «создано» для ревью с маркером, если оно уже есть; `None` - нет."""
        found = self._find_review_by_marker(reviews_url, installation_id, marker)
        if found is None:
            return None
        logger.warning(
            "прогон %s: ответ на POST %s потерян, ревью %s найдено по маркеру",
            review_run_id,
            reviews_url,
            found,
        )
        return {"id": found}

    def _find_review_by_marker(
        self, reviews_url: str, installation_id: int, marker: str
    ) -> str | None:
        """id ревью бота App с маркером в теле; ревью человека или чужого бота не в счёт."""
        bot_login = self._gw.bot_login()
        for review in self._gw._get_json_list_paginated(reviews_url, installation_id):
            user = review.get("user")
            body = review.get("body")
            if (
                isinstance(user, dict)
                and user.get("login") == bot_login
                and user.get("type") == "Bot"
                and isinstance(body, str)
                and marker in body
                and review.get("id") is not None
            ):
                return str(review["id"])
        return None

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
