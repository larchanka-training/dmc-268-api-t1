"""Ручной запуск ревью запроса на изменение (`POST /api/v1/reviews`, D2 change `manual-review-launch`).

Проверки — от дешёвого и не раскрывающего к дорогому:

1. репозиторий по id токеном пользователя мимо кэша видимости (`catalog.user_repository`):
   не отдан или публичный только с чтением — `REPOSITORY_NOT_FOUND`;
2. запись в сервисе: нет или выключен (`connected_at is None`) — тот же `REPOSITORY_NOT_FOUND`.
   GitHub спрошен до базы при любом id — ответ не выдаёт, зарегистрирован ли репозиторий;
3. роль push, triage, maintain или admin (`has_explicit_access`) из того же ответа GitHub —
   иначе `MANAGE_REQUIRED`;
4. номер установки приложения — иначе `NOT_INSTALLED`;
5. свежие метаданные PR от имени установки: `404` — `PULL_REQUEST_NOT_FOUND`, не открыт —
   `PULL_REQUEST_NOT_OPEN`, из форка от постороннего (правило вебхука,
   `is_trusted_pull_request`) — `PULL_REQUEST_UNTRUSTED`;
6. запись и постановка — общий с вебхуком `start_review_run`; дублем считается только
   активный прогон на коммит, поэтому проверенный коммит можно проверить снова. Активный
   прогон, в том числе победителя гонки, — `ALREADY_ACTIVE`.

Пауза авторевью (`auto_review_enabled = false`) и `branch_filter` запуск не останавливают:
это настройки автоматического ревью по вебхуку (D5). Прочий сбой GitHub (`VcsError`) и
закрытая сессия (`SessionClosed`) идут наверх — их переводит слой API.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.job_queue import JobQueue
from app.application.ports.run_outcome import NO_RUN_OUTCOME, RunOutcomeRecorder
from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.use_cases.connect_repository import UserContext
from app.application.use_cases.start_review_run import (
    RunLaunch,
    RunStart,
    start_review_run,
)
from app.application.use_cases.user_token import call_with_user_token
from app.domain.entities import ReviewRun
from app.domain.enums import MergeRequestState, Provider, TriggerSource
from app.domain.repository_access import has_explicit_access, visible_for_connect
from app.domain.vcs_errors import VcsNotFoundError
from app.domain.webhook import is_trusted_pull_request

logger = logging.getLogger(__name__)

# `ReviewJob.action` ручной постановки: отличает её в DLQ и логах, воркер по нему не ветвится (D6).
MANUAL_ACTION = "manual"


class LaunchRefusal(StrEnum):
    """Почему ревью не запущено; в HTTP несколько причин сходятся в один код (D7)."""

    REPOSITORY_NOT_FOUND = "repository_not_found"
    MANAGE_REQUIRED = "manage_required"
    NOT_INSTALLED = "not_installed"
    PULL_REQUEST_NOT_FOUND = "pull_request_not_found"
    PULL_REQUEST_NOT_OPEN = "pull_request_not_open"
    PULL_REQUEST_UNTRUSTED = "pull_request_untrusted"
    ALREADY_ACTIVE = "already_active"


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    """Что запустить: репозиторий у провайдера и номер PR в нём."""

    provider: Provider
    provider_repository_id: str
    pull_request_number: int


def launch_review(
    request: LaunchRequest,
    *,
    catalog: InstallationCatalog,
    installations: InstallationCatalog,
    vcs: VcsGateway,
    uow: UnitOfWork,
    queue: JobQueue,
    user: UserContext,
    now: Callable[[], datetime],
    new_id: Callable[[], UUID],
    run_outcome: RunOutcomeRecorder = NO_RUN_OUTCOME,
) -> RunStart | LaunchRefusal:
    """Запустить ревью head-коммита PR от имени пользователя сессии.

    Исход — `RunStart` с `kind = success` или причина отказа; отказ ничего не пишет и задачу не
    ставит. `catalog` спрашивается токеном пользователя, `installations` — от имени приложения.
    """
    if request.provider is not Provider.GITHUB:
        return LaunchRefusal.REPOSITORY_NOT_FOUND
    found = call_with_user_token(
        uow,
        user.oauth,
        user.session_id,
        now(),
        lambda token: catalog.user_repository(token, request.provider_repository_id),
    )
    if found is None or not visible_for_connect(found):
        return LaunchRefusal.REPOSITORY_NOT_FOUND
    with uow as work:
        # Id — из ответа GitHub: он канонический (`0101` и `101` — один репозиторий).
        repository = work.repositories.find_by_provider(Provider.GITHUB, found.provider_id)
    if repository is None or repository.connected_at is None:
        return LaunchRefusal.REPOSITORY_NOT_FOUND
    if not has_explicit_access(found.permissions):
        return LaunchRefusal.MANAGE_REQUIRED

    # Имя — свежее, из ответа GitHub: в базе оно то, что было при подключении.
    installation_id = installations.repository_installation_id(found.full_name)
    if installation_id is None:
        return LaunchRefusal.NOT_INSTALLED
    try:
        metadata = vcs.fetch_pr_metadata(found.full_name, request.pull_request_number, installation_id)
    except VcsNotFoundError:
        return LaunchRefusal.PULL_REQUEST_NOT_FOUND
    if metadata.state is not MergeRequestState.OPEN:
        return LaunchRefusal.PULL_REQUEST_NOT_OPEN
    if not is_trusted_pull_request(metadata.from_fork, metadata.author_association):
        logger.info(
            "PR не ревьюится: автор не свой",
            extra={
                "repo_full_name": found.full_name,
                "pr_number": request.pull_request_number,
                "author_association": metadata.author_association,
            },
        )
        return LaunchRefusal.PULL_REQUEST_UNTRUSTED

    start = start_review_run(
        uow,
        queue,
        vcs,
        repository,
        RunLaunch(
            trigger=TriggerSource.MANUAL,
            action=MANUAL_ACTION,
            pr_number=request.pull_request_number,
            head_sha=metadata.head_sha,
            installation_id=installation_id,
            repository_full_name=found.full_name,
            repository_provider_id=repository.provider_id,
        ),
        metadata,
        find_duplicate=_active_run,
        now=now,
        new_id=new_id,
        run_outcome=run_outcome,
    )
    if start.kind in ("duplicate", "conflict"):
        return LaunchRefusal.ALREADY_ACTIVE
    return start


def _active_run(work: UnitOfWork, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
    """Дубль ручного запуска — только активный прогон: завершённый коммит проверяется снова."""
    return work.review_runs.find_active(merge_request_id, head_sha)
