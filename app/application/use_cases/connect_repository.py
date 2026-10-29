"""Подключение репозитория к сервису: список репозиториев и регистрация.

Провайдер скрыт за портом `InstallationCatalog`, токен пользователя — за
`call_with_user_token` (`app/application/use_cases/user_token.py`), хранение —
за `UnitOfWork`. Время и id новой записи приходят аргументами. Запросы к
провайдеру идут вне транзакции, кроме обмена refresh token: соединение с
базой не ждёт GitHub. Сбой провайдера (`VcsError`, `OAuthProviderUnavailable`)
и закрытая сессия (`SessionClosed`) проходят наверх — их переводит слой API.

Что пользователь видит, решает GitHub: список — ответ `/user/installations…`
по токену пользователя, то есть пересечение «доступно приложению» и
«доступно пользователю», подключённые и нет вместе (change `repositories-one-list`). Подключение — `GET /repositories/{id}` токеном
пользователя и проверка установки приложения на репозитории. Подключать — с
правом admin или maintain (`app/domain/repository_access.py`).
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
from app.domain.repository_access import can_connect, visible_for_connect


class ConnectRefusal(StrEnum):
    """Почему репозиторий не подключён."""

    NOT_IN_INSTALLATION = "not_in_installation"
    ALREADY_CONNECTED = "already_connected"
    MANAGE_REQUIRED = "manage_required"


@dataclass(frozen=True, slots=True)
class ListedRepository:
    repository: InstallationRepository
    # Запись в сервисе, если репозиторий подключали (в том числе выключенный); `None` — нет.
    record: Repository | None
    # Подсказка интерфейсу, а не защита: `connect_repository` проверяет права сам (ui#45).
    can_connect: bool


@dataclass(frozen=True, slots=True)
class UserContext:
    """Чьими правами спрашиваем провайдера: сессия, к которой привязан токен."""

    oauth: OAuthProvider
    session_id: UUID


def list_with_connections(
    catalog: InstallationCatalog, uow: UnitOfWork, user: UserContext, now: datetime
) -> list[ListedRepository]:
    """Репозитории установок, видимые пользователю, подключённые и нет, — по имени.

    Подключён ли каждый, читается из базы одним запросом; может ли пользователь его
    подключить — по правам из того же ответа GitHub. Порядок — `full_name` без учёта
    регистра: у GitHub он уникален, страницы стабильны.
    """
    repositories = list_user_repositories(catalog, uow, user, now)
    with uow:
        provider_ids = [repository.provider_id for repository in repositories]
        records = {r.provider_id: r for r in uow.repositories.list_by_provider_ids(Provider.GITHUB, provider_ids)}
    listed = [
        ListedRepository(r, record=records.get(r.provider_id), can_connect=can_connect(r.permissions))
        for r in repositories
    ]
    return sorted(listed, key=lambda item: item.repository.full_name.casefold())


def list_user_repositories(
    catalog: InstallationCatalog, uow: UnitOfWork, user: UserContext, now: datetime
) -> list[InstallationRepository]:
    """Репозитории установок, видимые пользователю у провайдера, — ответ GitHub без чтения своей базы.

    Видимость прогонов (GET /reviews, GET /events) сопоставляет их с подключёнными
    сама, одним запросом, — запись в сервисе ей не нужна.
    """
    return call_with_user_token(uow, user.oauth, user.session_id, now, catalog.list_repositories)


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

    Два запроса к провайдеру вместо обхода всех установок: репозиторий по id
    токеном пользователя и установка приложения на нём. Порядок проверок: не
    виден пользователю (в том числе публичный только с правом чтения — так GitHub
    отдаёт любой публичный репозиторий) или приложение не установлено (приостановлено) —
    `NOT_IN_INSTALLATION`, один отказ без различия; нет права admin или
    maintain — `MANAGE_REQUIRED`; уже подключён — `ALREADY_CONNECTED`. Права проверяются раньше «уже подключён»:
    пользователь без прав не узнаёт, подключён ли репозиторий.
    """
    if provider is not Provider.GITHUB:
        return ConnectRefusal.NOT_IN_INSTALLATION
    # Сначала доступ пользователя, потом установка приложения: на чужой
    # репозиторий ответ один и тот же, установлено туда приложение или нет
    # (design D3 change `user-token-listing-edges`).
    found = call_with_user_token(
        uow, user.oauth, user.session_id, now, lambda token: catalog.user_repository(token, provider_id)
    )
    # Публичный только с `pull` — так GitHub отдаёт его кому угодно: не виден, и
    # установку не проверяем (change `user-token-explicit-access`). Приватный с ролью
    # read виден: дальше `403` (change `user-token-private-read`).
    if found is None or not visible_for_connect(found) or not catalog.app_installed(found.full_name):
        return ConnectRefusal.NOT_IN_INSTALLATION
    if not can_connect(found.permissions):
        return ConnectRefusal.MANAGE_REQUIRED
    # Id — из ответа GitHub, а не из запроса: GitHub разбирает id как число, и
    # запись `0101` не совпала бы с `str(id)` вебхуков и списков (change `canonical-repository-id`).
    repository = Repository(
        id=repository_id,
        provider=provider,
        provider_id=found.provider_id,
        full_name=found.full_name,
        default_branch=found.default_branch,
        auto_review_enabled=True,
        created_at=now,
        updated_at=now,
    )
    with uow:
        if uow.repositories.find_by_provider(provider, repository.provider_id) is not None:
            return ConnectRefusal.ALREADY_CONNECTED
        # Параллельное подключение могло успеть между проверкой и вставкой.
        if not uow.repositories.add(repository):
            return ConnectRefusal.ALREADY_CONNECTED
        uow.commit()
    return repository

