"""Репозитории установок GitHub App, видимые пользователю.

Структурный протокол, как и остальные порты: адаптер его не импортирует.
Отдельный от `VcsGateway`: это операции от имени пользователя (и проверка
установки от имени приложения), а не над запросом на изменение. Что
пользователь видит и что может, решает провайдер по токену пользователя
(change `repositories-via-user-token`); подключать ли — use case.
Сбой провайдера — `VcsError` из `app.domain.vcs_errors`; отвергнутый токен
пользователя — `ProviderTokenRejected` из `app.domain.provider_token`.
"""

from typing import Protocol

from app.domain.entities import InstallationRepository, Secret
from app.domain.enums import RepositorySelection


class InstallationCatalog(Protocol):
    def list_repositories(self, access_token: Secret) -> list[InstallationRepository]:
        """Репозитории всех установок приложения, доступные владельцу токена, с его правами."""
        ...

    def user_repository(self, access_token: Secret, provider_id: str) -> InstallationRepository | None:
        """Репозиторий по id так, как его видит владелец токена, с его правами; не виден — `None`."""
        ...

    def installation_selection(self, access_token: Secret, installation_id: int) -> RepositorySelection | None:
        """Выбор репозиториев установки, если она среди установок владельца токена и не
        приостановлена; иначе `None`."""
        ...

    def installation_repositories(self, access_token: Secret, installation_id: int) -> list[InstallationRepository]:
        """Репозитории одной установки, доступные владельцу токена, с его правами."""
        ...

    def app_installed(self, full_name: str) -> bool:
        """Установлено ли приложение на репозиторий и не приостановлена ли установка."""
        ...
