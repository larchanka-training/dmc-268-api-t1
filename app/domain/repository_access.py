"""Кто может подключить репозиторий к сервису — по правам, которые отдал провайдер.

Чистая функция: права приходят аргументом. Видеть репозиторий в списке
пользователь может, если GitHub вернул его в ответе по токену пользователя,
подключать — с правом admin или maintain (решение 3A из
larchanka-training/dmc-268-ui-t1#37, change `repositories-via-user-token`).
"""

from app.domain.entities import RepositoryPermissions


def can_connect(permissions: RepositoryPermissions) -> bool:
    """Может ли пользователь подключить репозиторий к сервису.

    Maintain по замыслу GitHub — управление репозиторием без опасных настроек
    (удаление, видимость, передача, доступы); подключение к ревью — такое действие.
    """
    return permissions.admin or permissions.maintain
