"""Репозитории установок GitHub App, видимые пользователю.

Структурный протокол, как и остальные порты: адаптер его не импортирует.
Отдельный от `VcsGateway`: это операции от имени пользователя, а не
приложения над запросом на изменение. Запрос идёт с токеном пользователя, и
что показать, решает провайдер: пересечение «доступно приложению» и
«доступно пользователю» (change `repositories-via-user-token`). Сбой
провайдера — `VcsError` из `app.domain.vcs_errors`; отвергнутый токен —
`ProviderTokenRejected` из `app.domain.provider_token`.
"""

from typing import Protocol

from app.domain.entities import InstallationRepository, Secret


class InstallationCatalog(Protocol):
    def list_repositories(self, access_token: Secret) -> list[InstallationRepository]:
        """Репозитории всех установок приложения, доступные владельцу токена, с его правами."""
        ...
