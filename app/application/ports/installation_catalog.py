"""Установки GitHub App: какие репозитории можно подключить и кто вправе это сделать.

Структурный протокол, как и остальные порты: адаптер его не импортирует.
Отдельный от `VcsGateway`: это операции уровня приложения, а не запроса на
изменение (change `implement-repository-connect`, design D2). Сбой провайдера —
`VcsError` из `app.domain.vcs_errors`.
"""

from typing import Protocol

from app.domain.entities import InstallationRepository
from app.domain.enums import RepositoryRole


class InstallationCatalog(Protocol):
    def list_repositories(self) -> list[InstallationRepository]:
        """Репозитории всех установок приложения."""
        ...

    def user_role(self, repository: InstallationRepository, login: str) -> RepositoryRole | None:
        """Роль `login` на репозитории у провайдера; `None` — доступа нет."""
        ...
