"""Сверка `thread_resolved` с состоянием треда на GitHub."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway


def sync_open_threads_from_host(
    merge_request_id: UUID,
    *,
    repo_full_name: str,
    pr_number: int,
    installation_id: int,
    uow: UnitOfWork,
    vcs: VcsGateway,
    now: datetime,
) -> None:
    """Обновить `thread_resolved` в БД, если тред уже закрыт на хостинге."""
    with uow:
        open_threads = uow.published_comments.list_open_threads_for_merge_request(
            merge_request_id
        )
    for thread in open_threads:
        resolved_on_host = vcs.is_review_thread_resolved(
            repo_full_name,
            pr_number,
            installation_id,
            thread.root_provider_comment_id,
        )
        if resolved_on_host is not True:
            continue
        with uow:
            root = uow.published_comments.get(thread.root_published_comment_id)
            if root is None or root.thread_resolved:
                continue
            uow.published_comments.update(
                replace(root, thread_resolved=True, updated_at=now)
            )
            uow.commit()


def apply_review_thread_webhook(
    payload: dict[str, object],
    *,
    uow: UnitOfWork,
    now: Callable[[], datetime],
) -> bool:
    """Вебхук `pull_request_review_thread`: resolved / unresolved."""
    action = payload.get("action")
    if action not in ("resolved", "unresolved"):
        return False
    comment = payload.get("comment")
    if not isinstance(comment, dict):
        return False
    raw_id = comment.get("id")
    if raw_id is None:
        return False
    provider_comment_id = str(raw_id)
    resolved = action == "resolved"
    with uow:
        published = uow.published_comments.find_by_provider_comment_id(provider_comment_id)
        if published is None:
            return False
        if published.thread_resolved == resolved:
            return True
        moment = now()
        uow.published_comments.update(
            replace(published, thread_resolved=resolved, updated_at=moment)
        )
        uow.commit()
    return True
