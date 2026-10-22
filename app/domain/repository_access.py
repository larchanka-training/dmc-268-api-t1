"""Что пользователь может делать с репозиторием установки, по его роли у провайдера.

Чистые функции: роль приходит аргументом, `None` — доступа к репозиторию нет
(change `repository-access-roles`, design D1 и D3). Это уточнение решения 3A
из larchanka-training/dmc-268-ui-t1#37: видеть в списке — любой доступ,
подключать — admin или maintain.
"""

from app.domain.enums import RepositoryRole

# Maintain по замыслу GitHub — управление репозиторием без опасных настроек
# (удаление, видимость, передача, доступы); подключение к ревью — такое действие.
_CONNECTING_ROLES = frozenset({RepositoryRole.ADMIN, RepositoryRole.MAINTAIN})


def can_see(role: RepositoryRole | None) -> bool:
    """Показывать ли репозиторий в списке: пользователь и так видит его у провайдера."""
    return role is not None


def can_connect(role: RepositoryRole | None) -> bool:
    """Может ли пользователь подключить репозиторий к сервису."""
    return role in _CONNECTING_ROLES
