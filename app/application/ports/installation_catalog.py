"""Установки GitHub App: какие репозитории можно подключить и кто вправе это сделать.

Структурный протокол, как и остальные порты: адаптер его не импортирует.
Отдельный от `VcsGateway`: это операции уровня приложения, а не запроса на
изменение (change `implement-repository-connect`, design D2). Сбой провайдера —
`VcsError` из `app.domain.vcs_errors`.
"""

from typing import Protocol

from app.domain.entities import InstallationRepository


class InstallationCatalog(Protocol):
    def list_repositories(self) -> list[InstallationRepository]:
        """Репозитории всех установок приложения."""
        ...

    def is_admin(self, repository: InstallationRepository, login: str) -> bool:
        """Есть ли у пользователя `login` права admin на репозиторий у провайдера."""
        ...
