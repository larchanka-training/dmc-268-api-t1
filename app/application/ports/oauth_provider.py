"""Провайдер входа через OAuth.

Структурный протокол, как и остальные порты: адаптер его не импортирует.
`exchange_code` отдаёт профиль и токен пользователя: токен сохраняется при
сессии зашифрованным и нужен ручкам репозиториев (change
`repositories-via-user-token`). `refresh` обменивает refresh token на новый
токен. Сбой провайдера — `OAuthProviderUnavailable` из `app.domain.login`;
отказ провайдера в refresh token — `ProviderTokenRejected` из
`app.domain.provider_token`.
"""

from typing import Protocol

from app.domain.entities import OAuthLogin, ProviderGrant, Secret
from app.domain.enums import Provider


class OAuthProvider(Protocol):
    provider: Provider

    def authorize_url(self, state: str) -> str:
        """Адрес страницы авторизации провайдера с этим `state`."""
        ...

    def exchange_code(self, code: str) -> OAuthLogin:
        """Обменять `code` на токен пользователя и вернуть профиль вместе с токеном."""
        ...

    def refresh(self, refresh_token: Secret) -> ProviderGrant:
        """Обменять refresh token на новый токен пользователя."""
        ...

    def revoke(self, access_token: Secret) -> None:
        """Отозвать токен пользователя у провайдера; сбой — `OAuthProviderUnavailable`."""
        ...
