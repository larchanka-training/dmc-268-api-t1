"""Провайдер входа через OAuth.

Структурный протокол, как и остальные порты: адаптер его не импортирует.
Токен пользователя наружу не выходит — `exchange_code` сразу отдаёт профиль,
поэтому сохранить токен вызывающему просто нечего. Сбой обмена или запроса
профиля — `OAuthProviderUnavailable` из `app.domain.login`.
"""

from typing import Protocol

from app.domain.entities import OAuthProfile
from app.domain.enums import Provider


class OAuthProvider(Protocol):
    provider: Provider

    def authorize_url(self, state: str) -> str:
        """Адрес страницы авторизации провайдера с этим `state`."""
        ...

    def exchange_code(self, code: str) -> OAuthProfile:
        """Обменять `code` на токен пользователя и вернуть его профиль."""
        ...
