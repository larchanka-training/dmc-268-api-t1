"""Подключение репозитория к сервису: список доступных и регистрация.

Провайдер скрыт за портом `InstallationCatalog`, хранение — за `UnitOfWork`.
Время и id новой записи приходят аргументами. Запросы к провайдеру идут вне
транзакции: соединение с базой не ждёт GitHub. Сбой провайдера (`VcsError`)
проходит наверх — его переводит слой API. Видеть в списке пользователь может
репозитории, к которым у него есть любой доступ у провайдера, подключать — с
ролью admin или maintain (`app/domain/repository_access.py`, change
`repository-access-roles`).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.unit_of_work import UnitOfWork
from app.domain.entities import InstallationRepository, Repository
from app.domain.enums import Provider
from app.domain.repository_access import can_connect, can_see


class ConnectRefusal(StrEnum):
    """Почему репозиторий не подключён."""

    NOT_IN_INSTALLATION = "not_in_installation"
    ALREADY_CONNECTED = "already_connected"
    MANAGE_REQUIRED = "manage_required"


@dataclass(frozen=True, slots=True)
class AvailableRepository:
    repository: InstallationRepository
    already_connected: bool


def list_available_repositories(
    catalog: InstallationCatalog, uow: UnitOfWork, login: str
) -> list[AvailableRepository]:
    """Репозитории установок, к которым у `login` есть доступ у провайдера, с отметкой,
    зарегистрирован ли каждый.

    Остальные в список не попадают: пользователь видит только то, что и так видит
    у провайдера, и имена чужих приватных репозиториев ему не раскрываются. Роль —
    запрос на каждый репозиторий, последовательно; сбой любого из них проходит
    наверх: частичный список выдал бы сбой GitHub за отсутствие доступа. Стоимость
    и отказ от кэша — design D2 change `repository-access-roles`.
    """
    repositories = [r for r in catalog.list_repositories() if can_see(catalog.user_role(r, login))]
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

    Порядок проверок — design D4 change `repository-access-roles`: нет в
    установке, нет роли admin или maintain, уже подключён. Права проверяются
    раньше «уже подключён»: пользователь без прав получает отказ по правам и не
    узнаёт, подключён ли репозиторий.
    """
    if provider is not Provider.GITHUB:
        return ConnectRefusal.NOT_IN_INSTALLATION
    found = next((r for r in catalog.list_repositories() if r.provider_id == provider_id), None)
    if found is None:
        return ConnectRefusal.NOT_IN_INSTALLATION
    if not can_connect(catalog.user_role(found, login)):
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
