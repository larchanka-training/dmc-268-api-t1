"""Подключение репозитория к сервису: список доступных и регистрация.

Провайдер скрыт за портом `InstallationCatalog`, токен пользователя — за
`call_with_user_token` (`app/application/use_cases/user_token.py`), хранение —
за `UnitOfWork`. Время и id новой записи приходят аргументами. Запросы к
провайдеру идут вне транзакции, кроме обмена refresh token: соединение с
базой не ждёт GitHub. Сбой провайдера (`VcsError`, `OAuthProviderUnavailable`)
и закрытая сессия (`SessionClosed`) проходят наверх — их переводит слой API.

Что пользователь видит, решает GitHub: список — ответ `/user/installations…`
по токену пользователя, то есть пересечение «доступно приложению» и
«доступно пользователю». Подключать — с правом admin или maintain
(`app/domain/repository_access.py`, change `repositories-via-user-token`).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.oauth_provider import OAuthProvider
from app.application.ports.unit_of_work import UnitOfWork
from app.application.use_cases.user_token import call_with_user_token
from app.domain.entities import InstallationRepository, Repository
from app.domain.enums import Provider
from app.domain.repository_access import can_connect


class ConnectRefusal(StrEnum):
    """Почему репозиторий не подключён."""

    NOT_IN_INSTALLATION = "not_in_installation"
    ALREADY_CONNECTED = "already_connected"
    MANAGE_REQUIRED = "manage_required"


@dataclass(frozen=True, slots=True)
class AvailableRepository:
    repository: InstallationRepository
    already_connected: bool


@dataclass(frozen=True, slots=True)
class UserContext:
    """Чьими правами спрашиваем провайдера: сессия, к которой привязан токен."""

    oauth: OAuthProvider
    session_id: UUID


def list_available_repositories(
    catalog: InstallationCatalog, uow: UnitOfWork, user: UserContext, now: datetime
) -> list[AvailableRepository]:
    """Репозитории установок, видимые пользователю, с отметкой, зарегистрирован ли каждый."""
    repositories = _user_repositories(catalog, uow, user, now)
    with uow:
        return [
            AvailableRepository(
                repository,
                already_connected=uow.repositories.find_by_provider(Provider.GITHUB, repository.provider_id)
                is not None,
            )
            for repository in repositories
        ]


def connect_repository(
    catalog: InstallationCatalog,
    uow: UnitOfWork,
    user: UserContext,
    provider: Provider,
    provider_id: str,
    repository_id: UUID,
    now: datetime,
) -> Repository | ConnectRefusal:
    """Зарегистрировать репозиторий установки от имени пользователя сессии.

    Порядок проверок: нет среди видимых пользователю — `NOT_IN_INSTALLATION`
    (и «нет в установках», и «не виден пользователю» — один отказ, без
    различия), нет права admin или maintain — `MANAGE_REQUIRED`, уже
    подключён — `ALREADY_CONNECTED`. Права проверяются раньше «уже подключён»:
    пользователь без прав не узнаёт, подключён ли репозиторий.
    """
    if provider is not Provider.GITHUB:
        return ConnectRefusal.NOT_IN_INSTALLATION
    found = next((r for r in _user_repositories(catalog, uow, user, now) if r.provider_id == provider_id), None)
    if found is None:
        return ConnectRefusal.NOT_IN_INSTALLATION
    if not can_connect(found.permissions):
        return ConnectRefusal.MANAGE_REQUIRED
    repository = Repository(
        id=repository_id,
        provider=provider,
        provider_id=provider_id,
        full_name=found.full_name,
        default_branch=found.default_branch,
        auto_review_enabled=True,
        created_at=now,
        updated_at=now,
    )
    with uow:
        if uow.repositories.find_by_provider(provider, provider_id) is not None:
            return ConnectRefusal.ALREADY_CONNECTED
        # Параллельное подключение могло успеть между проверкой и вставкой.
        if not uow.repositories.add(repository):
            return ConnectRefusal.ALREADY_CONNECTED
        uow.commit()
    return repository


def _user_repositories(
    catalog: InstallationCatalog, uow: UnitOfWork, user: UserContext, now: datetime
) -> list[InstallationRepository]:
    return call_with_user_token(uow, user.oauth, user.session_id, now, catalog.list_repositories)
