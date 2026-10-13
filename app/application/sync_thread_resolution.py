"""Сверка `thread_resolved` с состоянием треда на GitHub."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from app.application.merge_gate_publish import refresh_merge_gate_for_merge_request
from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.domain.enums import CommentKind, Provider


def _thread_comment_provider_ids(thread: Mapping[str, Any]) -> tuple[str, ...]:
    raw_comments = thread.get("comments")
    if not isinstance(raw_comments, list):
        return ()
    ids: list[str] = []
    for item in raw_comments:
        if isinstance(item, dict) and item.get("id") is not None:
            ids.append(str(item["id"]))
    return tuple(ids)


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
    if not open_threads:
        return
    root_ids = frozenset(t.root_provider_comment_id for t in open_threads)
    resolved_map = vcs.fetch_review_threads_resolved_state(
        repo_full_name, pr_number, installation_id, root_ids
    )
    for thread in open_threads:
        if resolved_map.get(thread.root_provider_comment_id) is not True:
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
    payload: Mapping[str, Any],
    *,
    uow: UnitOfWork,
    now: Callable[[], datetime],
) -> bool:
    """Вебхук `pull_request_review_thread`: resolved / unresolved."""
    action = payload.get("action")
    if action not in ("resolved", "unresolved"):
        return False
    thread = payload.get("thread")
    if not isinstance(thread, dict):
        return False
    comment_ids = _thread_comment_provider_ids(thread)
    if not comment_ids:
        return False
    resolved = action == "resolved"
    with uow:
        published = None
        for provider_comment_id in comment_ids:
            row = uow.published_comments.find_by_provider_comment_id(provider_comment_id)
            if row is not None and row.kind is CommentKind.INLINE:
                published = row
                break
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


def handle_review_thread_webhook(
    payload: Mapping[str, Any],
    *,
    uow: UnitOfWork,
    vcs: VcsGateway,
    now: Callable[[], datetime],
) -> bool:
    """Обновить тред и сразу перевыставить merge gate на head PR."""
    if not apply_review_thread_webhook(payload, uow=uow, now=now):
        return False
    pull_request = payload.get("pull_request")
    repository = payload.get("repository")
    installation = payload.get("installation")
    if not isinstance(pull_request, dict) or not isinstance(repository, dict):
        return True
    if not isinstance(installation, dict) or installation.get("id") is None:
        return True
    head = pull_request.get("head")
    if not isinstance(head, dict) or head.get("sha") is None:
        return True
    pr_number = pull_request.get("number")
    if not isinstance(pr_number, int):
        return True
    repo_full_name = repository.get("full_name")
    provider_repo_id = repository.get("id")
    if not isinstance(repo_full_name, str) or provider_repo_id is None:
        return True
    installation_id = int(installation["id"])
    head_sha = str(head["sha"])
    with uow:
        repo = uow.repositories.find_by_provider(Provider.GITHUB, str(provider_repo_id))
        if repo is None:
            return True
        merge_request = uow.merge_requests.find_by_number(repo.id, pr_number)
        if merge_request is None:
            return True
        merge_request_id = merge_request.id
    refresh_merge_gate_for_merge_request(
        merge_request_id,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_sha=head_sha,
        installation_id=installation_id,
        uow=uow,
        vcs=vcs,
        now=now(),
    )
    return True
