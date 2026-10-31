"""Сверка `thread_resolved` с состоянием треда на GitHub."""

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from app.application.merge_gate_publish import (
    DISABLED_REVIEW_CHECK,
    build_merge_gate_check,
    collect_open_findings,
    settle_merge_gate,
)
from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import ReviewThreadState
from app.domain.enums import CommentKind, Provider, ReviewRunStatus
from app.domain.webhook import THREAD_ACTIONS

logger = logging.getLogger(__name__)


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
    states = vcs.fetch_review_threads_state(
        repo_full_name, pr_number, installation_id, root_ids
    )
    record_threads_resolved_on_host(
        ((t.root_provider_comment_id, t.root_published_comment_id) for t in open_threads),
        states,
        uow=uow,
        now=now,
    )


def record_threads_resolved_on_host(
    roots: Iterable[tuple[str, UUID]],
    states: Mapping[str, ReviewThreadState],
    *,
    uow: UnitOfWork,
    now: datetime,
) -> frozenset[str]:
    """Записать закрытие тредов, открытых в базе и закрытых на хостинге.

    `roots` - пары (id корня у провайдера, id записи корня). Бот треды не
    закрывает, поэтому закрытие записывается как закрытие человеком.
    Возвращает id корней, которые записаны закрытыми.
    """
    marked: set[str] = set()
    for provider_comment_id, published_comment_id in roots:
        state = states.get(provider_comment_id)
        if state is None or not state.resolved:
            continue
        with uow:
            root = uow.published_comments.get(published_comment_id)
            if root is None or root.thread_resolved:
                continue
            uow.published_comments.update(
                replace(root, thread_resolved=True, thread_resolved_by_bot=False, updated_at=now)
            )
            uow.commit()
        marked.add(provider_comment_id)
    return frozenset(marked)


def refresh_merge_gate_for_merge_request(
    merge_request_id: UUID,
    *,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    installation_id: int,
    uow: UnitOfWork,
    vcs: VcsGateway,
    now: datetime,
) -> None:
    sync_open_threads_from_host(
        merge_request_id,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        installation_id=installation_id,
        uow=uow,
        vcs=vcs,
        now=now,
    )
    with uow:
        head_run = uow.review_runs.find_latest(merge_request_id, head_sha)
    if head_run is None or head_run.status is not ReviewRunStatus.COMPLETED:
        # Head не проверен: ревью идёт, упало или отменено сменой целевой ветки
        # (#222). Gate выставит прогон, который его проверит; до тех пор пересчёт
        # не даёт `success` непроверенному коммиту.
        logger.info(
            "merge gate %s@%s не пересчитан: последний прогон на head - %s",
            repo_full_name,
            head_sha,
            head_run.status if head_run is not None else "нет",
        )
        return
    still_open = collect_open_findings(uow, merge_request_id, head_run_id=head_run.id)
    vcs.set_merge_gate_check(
        repo_full_name,
        head_sha,
        installation_id,
        build_merge_gate_check(still_open),
    )
    # Целевую ветку могли сменить после чтения прогона выше (#222).
    settle_merge_gate(
        uow,
        vcs,
        merge_request_id=merge_request_id,
        repo_full_name=repo_full_name,
        head_sha=head_sha,
        installation_id=installation_id,
        run_id=head_run.id,
    )


def apply_review_thread_webhook(
    payload: Mapping[str, Any],
    *,
    uow: UnitOfWork,
    now: Callable[[], datetime],
) -> bool:
    """Вебхук `pull_request_review_thread`: resolved / unresolved."""
    action = payload.get("action")
    if action not in THREAD_ACTIONS:
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
        # Бот треды не закрывает: закрытие - человеком. Метку бота у старых
        # записей не трогает повторный вебхук того же состояния (выход выше);
        # открытый заново тред снова ничей.
        uow.published_comments.update(
            replace(
                published,
                thread_resolved=resolved,
                thread_resolved_by_bot=False,
                updated_at=moment,
            )
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
    """Обновить тред и сразу перевыставить merge gate на head PR.

    У выключенного репозитория (`connected_at IS NULL`) тред записывается так же, но на head
    ставится `DISABLED_REVIEW_CHECK`: пересчёт по тредам поставил бы `failure` на head с
    проверкой «Ревью выключено» или `success` «Ревью пройдено» на непроверенный head.
    """
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
        disabled = repo.connected_at is None
        merge_request = uow.merge_requests.find_by_number(repo.id, pr_number)
        if merge_request is None:
            return True
        merge_request_id = merge_request.id
    if disabled:
        vcs.set_merge_gate_check(repo_full_name, head_sha, installation_id, DISABLED_REVIEW_CHECK)
        return True
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
