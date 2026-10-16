"""Вход через OAuth самого GitHub App.

Два запроса: обмен `code` на токен пользователя секретом приложения и
`GET /user` с этим токеном. Токен живёт только внутри `exchange_code` и
наружу не выходит. У OAuth GitHub App нет scope: приложение получает только
профиль.

Любой сбой — сеть, статус, ответ без нужных полей, `error` в ответе обмена
(неверный `code` GitHub отдаёт со статусом 200) — `OAuthProviderUnavailable`.
В сообщение не попадают ни `code`, ни токен, ни секрет.
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx

from app.domain.entities import OAuthProfile
from app.domain.enums import Provider
from app.domain.login import OAuthProviderUnavailable

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"


class GitHubOAuthProvider:
    provider = Provider.GITHUB

    def __init__(
        self, client_id: str, client_secret: str, callback_url: str, client: httpx.Client
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._callback_url = callback_url
        self._client = client

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {"client_id": self._client_id, "state": state, "redirect_uri": self._callback_url}
        )
        return f"{_AUTHORIZE_URL}?{query}"

    def exchange_code(self, code: str) -> OAuthProfile:
        token = self._exchange(code)
        user = self._json(
            "профиль",
            lambda: self._client.get(
                _USER_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                },
            ),
        )
        try:
            return OAuthProfile(
                provider_user_id=str(user["id"]),
                login=user["login"],
                name=user.get("name"),
                avatar_url=user.get("avatar_url"),
            )
        except (KeyError, TypeError) as error:
            raise OAuthProviderUnavailable("GitHub вернул профиль без id или login") from error

    def _exchange(self, code: str) -> str:
        body = self._json(
            "обмен code",
            lambda: self._client.post(
                _TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": self._callback_url,
                },
            ),
        )
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            # `error` — код вроде bad_verification_code, секретов в нём нет.
            reason = body.get("error") if isinstance(body, dict) else None
            raise OAuthProviderUnavailable(f"GitHub не выдал токен пользователя: {reason}")
        return token

    @staticmethod
    def _json(step: str, send: Callable[[], httpx.Response]) -> Any:
        try:
            response = send()
        except httpx.RequestError as error:
            # `from None`: исходное исключение держит запрос, а в нём `code`
            # и секрет приложения.
            raise OAuthProviderUnavailable(
                f"GitHub недоступен ({step}): {type(error).__name__}"
            ) from None
        if response.status_code != 200:
            raise OAuthProviderUnavailable(f"GitHub ответил {response.status_code} ({step})")
        try:
            return response.json()
        except ValueError:
            raise OAuthProviderUnavailable(f"GitHub ответил не JSON ({step})") from None
