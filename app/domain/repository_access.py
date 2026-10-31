"""Кто может подключить репозиторий к сервису — по правам, которые отдал провайдер.

Чистая функция: права приходят аргументом. Видеть репозиторий в списке
пользователь может, если GitHub вернул его в ответе по токену пользователя,
подключать — с правом admin или maintain (решение 3A из
larchanka-training/dmc-268-ui-t1#37, change `repositories-via-user-token`).
Публичный репозиторий только с правом чтения при подключении считается невидимым,
приватный — видимым (change `user-token-private-read`).
"""

from collections.abc import Collection, Iterable

from app.domain.entities import (
    InstallationRepository,
    Repository,
    RepositoryPermissions,
)
from app.domain.enums import ReviewPause


def has_explicit_access(permissions: RepositoryPermissions) -> bool:
    """Есть ли у пользователя явное право на репозиторий, кроме чтения.

    `GET /repositories/{id}` токеном пользователя открывает любой публичный
    репозиторий с одним `pull`: без явного права (push, triage, maintain, admin)
    он для подключения не виден, как и в списке `/user/installations…`, —
    иначе ответ выдаёт, установлено ли туда приложение
    (change `user-token-explicit-access`).
    """
    return permissions.admin or permissions.maintain or permissions.push or permissions.triage


def visible_for_connect(repository: InstallationRepository) -> bool:
    """Виден ли репозиторий пользователю при подключении.

    Приватный GitHub отдаёт только участнику, хотя бы с ролью read: пользователь
    и так знает, что репозиторий есть, и отказ `403` ему ничего не раскрывает.
    Публичный — только с явным правом (`has_explicit_access`)
    (change `user-token-private-read`).
    """
    return repository.private or has_explicit_access(repository.permissions)


def can_connect(permissions: RepositoryPermissions) -> bool:
    """Может ли пользователь подключить репозиторий к сервису.

    Maintain по замыслу GitHub — управление репозиторием без опасных настроек
    (удаление, видимость, передача, доступы); подключение к ревью — такое действие.
    """
    return permissions.admin or permissions.maintain


def to_connect_on_setup(
    repositories: Iterable[InstallationRepository], registered_provider_ids: Collection[str]
) -> list[InstallationRepository]:
    """Что подключить на возврате с установки «Only select repositories»: репозитории с правом
    admin или maintain, у которых ещё нет записи в сервисе.

    Выключенный (запись есть) не включается: выключение — явное решение пользователя
    (change `repositories-one-list`, design D7).
    """
    return [
        repository
        for repository in repositories
        if can_connect(repository.permissions) and repository.provider_id not in registered_provider_ids
    ]


def review_pause(repository: Repository) -> ReviewPause | None:
    """Почему ревью по вебхуку не идёт; `None` — идёт.

    Выключение репозитория важнее настройки: при обоих признаках — `DISCONNECTED`,
    чтобы пользователь чинил то, что действительно мешает (сначала подключение).
    """
    if repository.connected_at is None:
        return ReviewPause.DISCONNECTED
    if not repository.auto_review_enabled:
        return ReviewPause.AUTO_REVIEW_OFF
    return None
