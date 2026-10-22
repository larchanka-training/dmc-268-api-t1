"""Кто может подключить репозиторий к сервису — по правам, которые отдал провайдер.

Чистая функция: права приходят аргументом. Видеть репозиторий в списке
пользователь может, если GitHub вернул его в ответе по токену пользователя,
подключать — с правом admin или maintain (решение 3A из
larchanka-training/dmc-268-ui-t1#37, change `repositories-via-user-token`).
Репозиторий только с правом чтения при подключении считается невидимым.
"""

from app.domain.entities import RepositoryPermissions


def has_explicit_access(permissions: RepositoryPermissions) -> bool:
    """Есть ли у пользователя явное право на репозиторий, кроме чтения.

    `GET /repositories/{id}` токеном пользователя открывает любой публичный
    репозиторий с одним `pull`: без явного права (push, triage, maintain, admin)
    он для подключения не виден, как и в списке `/user/installations…`, —
    иначе ответ выдаёт, установлено ли туда приложение
    (change `user-token-explicit-access`).
    """
    return permissions.admin or permissions.maintain or permissions.push or permissions.triage


def can_connect(permissions: RepositoryPermissions) -> bool:
    """Может ли пользователь подключить репозиторий к сервису.

    Maintain по замыслу GitHub — управление репозиторием без опасных настроек
    (удаление, видимость, передача, доступы); подключение к ревью — такое действие.
    """
    return permissions.admin or permissions.maintain
