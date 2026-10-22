"""Подключение репозитория к сервису: список доступных и регистрация.

Провайдер скрыт за портом `InstallationCatalog`, хранение — за `UnitOfWork`.
Время и id новой записи приходят аргументами. Запросы к провайдеру идут вне
транзакции: соединение с базой не ждёт GitHub. Сбой провайдера (`VcsError`)
проходит наверх — его переводит слой API.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.unit_of_work import UnitOfWork
from app.domain.entities import InstallationRepository, Repository
from app.domain.enums import Provider


class ConnectRefusal(StrEnum):
    """Почему репозиторий не подключён."""

    NOT_IN_INSTALLATION = "not_in_installation"
    ALREADY_CONNECTED = "already_connected"
    ADMIN_REQUIRED = "admin_required"


@dataclass(frozen=True, slots=True)
class AvailableRepository:
    repository: InstallationRepository
    already_connected: bool


def list_available_repositories(catalog: InstallationCatalog, uow: UnitOfWork) -> list[AvailableRepository]:
    """Репозитории установок с отметкой, зарегистрирован ли каждый.

    По правам пользователя список не фильтруется: запрос прав на каждый
    репозиторий при каждом показе дорог, права проверяет подключение.
    """
    repositories = catalog.list_repositories()
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
    provider: Provider,
    provider_id: str,
    login: str,
    repository_id: UUID,
    now: datetime,
) -> Repository | ConnectRefusal:
    """Зарегистрировать репозиторий установки от имени пользователя `login`.

    Порядок проверок — design D4: нет в установке, уже подключён, не admin.
    Уже подключённый проверяется раньше прав: это экономит запрос к провайдеру
    и ничего не раскрывает — список доступных отдаёт отметку всем.
    """
    if provider is not Provider.GITHUB:
        return ConnectRefusal.NOT_IN_INSTALLATION
    found = next((r for r in catalog.list_repositories() if r.provider_id == provider_id), None)
    if found is None:
        return ConnectRefusal.NOT_IN_INSTALLATION
    with uow:
        if uow.repositories.find_by_provider(provider, provider_id) is not None:
            return ConnectRefusal.ALREADY_CONNECTED
    if not catalog.is_admin(found, login):
        return ConnectRefusal.ADMIN_REQUIRED
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
        # Параллельное подключение могло успеть между проверкой и вставкой.
        if not uow.repositories.add(repository):
            return ConnectRefusal.ALREADY_CONNECTED
        uow.commit()
    return repository
