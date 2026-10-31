"""Подключение репозитория к сервису: список репозиториев, регистрация и выключение.

Провайдер скрыт за портом `InstallationCatalog`, токен пользователя — за
`call_with_user_token` (`app/application/use_cases/user_token.py`), хранение —
за `UnitOfWork`. Время и id новой записи приходят аргументами. Запросы к
провайдеру идут вне транзакции, кроме обмена refresh token: соединение с
базой не ждёт GitHub. Сбой провайдера (`VcsError`, `OAuthProviderUnavailable`)
и закрытая сессия (`SessionClosed`) проходят наверх — их переводит слой API.

Что пользователь видит, решает GitHub: список — ответ `/user/installations…`
по токену пользователя, то есть пересечение «доступно приложению» и
«доступно пользователю», подключённые и нет вместе (change
`repositories-one-list`). Подключение — `GET /repositories/{id}` токеном
пользователя и проверка установки приложения на репозитории. Подключать — с
правом admin или maintain (`app/domain/repository_access.py`).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.oauth_provider import OAuthProvider
from app.application.ports.unit_of_work import UnitOfWork
from app.application.use_cases.user_token import call_with_user_token
from app.domain.entities import (
    InstallationRepository,
    Repository,
    RepositorySettings,
    Secret,
)
from app.domain.enums import Provider, RepositorySelection
from app.domain.repository_access import (
    can_connect,
    to_connect_on_setup,
    visible_for_connect,
)
from app.domain.vcs_errors import VcsError

_log = logging.getLogger(__name__)


class ConnectRefusal(StrEnum):
    """Почему репозиторий не подключён."""

    NOT_IN_INSTALLATION = "not_in_installation"
    ALREADY_CONNECTED = "already_connected"
    MANAGE_REQUIRED = "manage_required"
    # Записи в сервисе нет: удалили между проверкой видимости и записью.
    NOT_FOUND = "not_found"


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
    maintain — `MANAGE_REQUIRED`; уже подключён и включён — `ALREADY_CONNECTED`. Права проверяются раньше
    «уже подключён»: пользователь без прав не узнаёт, подключён ли репозиторий. Выключенный включается
    обратно: ответ — его прежняя запись.
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
        connected_at=now,
    )
    # Сначала включаем выключенную запись — прежний `id` и история остаются; её нет —
    # вставка. Обе операции сами разбирают гонку (design D4): проигравший параллельный
    # запрос не находит выключенной записи и упирается в ключ — `ALREADY_CONNECTED`.
    with uow:
        reconnected = uow.repositories.reconnect(repository)
        if reconnected is None and not uow.repositories.add(repository):
            return ConnectRefusal.ALREADY_CONNECTED
        uow.commit()
    return reconnected or repository


def _manage_refusal(
    catalog: InstallationCatalog, uow: UnitOfWork, user: UserContext, repository: Repository, now: datetime
) -> ConnectRefusal | None:
    """Может ли пользователь управлять репозиторием; `None` — может.

    Запрос к провайдеру мимо кэша видимости: роль, отозванная после кэширования, видна сразу.
    Провайдер репозиторий не отдаёт — `NOT_IN_INSTALLATION`; нет admin или maintain —
    `MANAGE_REQUIRED`.
    """
    found = call_with_user_token(
        uow, user.oauth, user.session_id, now, lambda token: catalog.user_repository(token, repository.provider_id)
    )
    if found is None:
        return ConnectRefusal.NOT_IN_INSTALLATION
    if not can_connect(found.permissions):
        return ConnectRefusal.MANAGE_REQUIRED
    return None


def disconnect_repository(
    catalog: InstallationCatalog, uow: UnitOfWork, user: UserContext, repository: Repository, now: datetime
) -> ConnectRefusal | None:
    """Выключить ревью видимого пользователю репозитория; `None` — выключено.

    Видимость по кэшу проверяет вызывающий, права — здесь, запросом к провайдеру мимо кэша,
    тем же правилом, что у подключения (design D6). Провайдер репозиторий не отдаёт —
    `NOT_IN_INSTALLATION`; нет admin или maintain — `MANAGE_REQUIRED`. Уже выключенный
    выключается ещё раз без ошибки.
    """
    refusal = _manage_refusal(catalog, uow, user, repository, now)
    if refusal is not None:
        return refusal
    with uow:
        uow.repositories.disconnect(repository.id, now)
        uow.commit()
    return None


def save_repository_settings(
    catalog: InstallationCatalog,
    uow: UnitOfWork,
    user: UserContext,
    repository: Repository,
    settings: RepositorySettings,
    now: datetime,
) -> Repository | ConnectRefusal:
    """Записать настройки ревью видимого пользователю репозитория и вернуть записанную запись.

    Права — тем же правилом и тем же запросом к провайдеру мимо кэша, что у выключения
    (design D6): не отдан — `NOT_IN_INSTALLATION`, нет admin или maintain —
    `MANAGE_REQUIRED`; при отказе ничего не пишется. Записи к моменту записи уже нет —
    `NOT_FOUND`. Замена полная, повтор того же тела
    даёт ту же запись. Выключенный репозиторий настройки получает, но не включается:
    `connected_at` не трогаем, включение — `connect_repository`.
    """
    refusal = _manage_refusal(catalog, uow, user, repository, now)
    if refusal is not None:
        return refusal
    with uow:
        saved = uow.repositories.update_settings(repository.id, settings, now)
        uow.commit()
    return saved if saved is not None else ConnectRefusal.NOT_FOUND


def connect_installation(
    catalog: InstallationCatalog,
    uow: UnitOfWork,
    user: UserContext,
    installation_id: int,
    new_id: Callable[[], UUID],
    now: datetime,
    *,
    connect_selected: bool,
) -> int | None:
    """Возврат с установки GitHub App (Setup URL): подключить выбранные репозитории установки.

    `installation_id` из адреса не доверяется — ищется среди установок пользователя по его
    токену; нет — `None`. Найдена — свежий список видимых пишется в кэш (`catalog` с
    `read=False`, design D3), best-effort: сбой обхода установок в лог, подключение идёт
    дальше. Подключает только `connect_selected` (`setup_action=install`) и только при «Only
    select repositories» — `to_connect_on_setup`: только вставка, выключенные не включаются
    (design D7), поэтому не `connect_repository`. Вернуть число подключённых.
    """

    def from_github(token: Secret) -> list[InstallationRepository] | None:
        selection = catalog.installation_selection(token, installation_id)
        if selection is None:
            return None
        # Ради кэша: репозитории новой установки сразу видны в списке. Нужен только кэшу,
        # поэтому сбой — не причина отказать в подключении по найденной установке.
        try:
            catalog.list_repositories(token)
        except VcsError:
            _log.warning("Setup URL: кэш видимости не обновлён, подключение продолжается", exc_info=True)
        if not connect_selected or selection is RepositorySelection.ALL:
            return []
        return catalog.installation_repositories(token, installation_id)

    repositories = call_with_user_token(uow, user.oauth, user.session_id, now, from_github)
    if repositories is None:
        return None
    connected = 0
    with uow:
        provider_ids = [repository.provider_id for repository in repositories]
        registered = {r.provider_id for r in uow.repositories.list_by_provider_ids(Provider.GITHUB, provider_ids)}
        for found in to_connect_on_setup(repositories, registered):
            repository = Repository(
                id=new_id(),
                provider=Provider.GITHUB,
                provider_id=found.provider_id,
                full_name=found.full_name,
                default_branch=found.default_branch,
                auto_review_enabled=True,
                created_at=now,
                updated_at=now,
                connected_at=now,
            )
            # Параллельный POST /repositories мог успеть: вставка его не перетирает.
            if uow.repositories.add(repository):
                connected += 1
        uow.commit()
    return connected
