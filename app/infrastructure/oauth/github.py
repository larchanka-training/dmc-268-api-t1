"""Вход через OAuth самого GitHub App и обновление токена пользователя.

Вход — два запроса: обмен `code` на токен пользователя (user access token
GitHub App) секретом приложения и `GET /user` с этим токеном. Профиль и
токен уходят вызывающему: токен хранится при сессии зашифрованным
(change `repositories-via-user-token`). У OAuth GitHub App нет scope: что
токен может, решают разрешения приложения и доступ пользователя.

Если у App включено истечение токенов, обмен отдаёт ещё `expires_in`,
`refresh_token` и `refresh_token_expires_in`; `refresh` обменивает refresh
token на новую пару тем же `POST /login/oauth/access_token` с
`grant_type=refresh_token` («Refreshing user access tokens», документация
GitHub Apps).

Любой сбой — сеть, статус, ответ без нужных полей, `error` в ответе обмена
(неверный `code` GitHub отдаёт со статусом 200) — `OAuthProviderUnavailable`.
Исключение — `bad_refresh_token` при обновлении: refresh token отозван, истёк
или уже израсходован, это `ProviderTokenRejected`, и пользователь входит
заново. В сообщение не попадают ни `code`, ни токены, ни секрет.
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx

from app.domain.entities import OAuthLogin, OAuthProfile, ProviderGrant, Secret
from app.domain.enums import Provider
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_token import ProviderTokenRejected

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"
# Код ошибки обмена refresh token, означающий «этот refresh token больше не
# действует»; остальные коды — неверная настройка приложения, а не сессии.
_BAD_REFRESH_TOKEN = "bad_refresh_token"


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
        # Пустые настройки дали бы редирект к GitHub с пустым `client_id` и
        # относительным `redirect_uri`; секреты в сообщение не попадают.
        if not self._client_id:
            raise OAuthProviderUnavailable("не задан GITHUB_APP_CLIENT_ID")
        if not self._callback_url.startswith(("https://", "http://")):
            raise OAuthProviderUnavailable("PUBLIC_ORIGIN пуст или без схемы http(s)")
        query = urlencode(
            {"client_id": self._client_id, "state": state, "redirect_uri": self._callback_url}
        )
        return f"{_AUTHORIZE_URL}?{query}"

    def __repr__(self) -> str:
        # Секрет приложения в repr не попадает — и в трейсбек с локальными тоже.
        return f"GitHubOAuthProvider(client_id={self._client_id!r})"

    def exchange_code(self, code: str) -> OAuthLogin:
        grant = self._grant(
            "обмен code",
            {"code": code, "redirect_uri": self._callback_url},
        )
        if isinstance(grant, str):
            raise OAuthProviderUnavailable(f"GitHub не выдал токен пользователя: {grant}")
        token = grant.access_token.reveal()
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
            profile = OAuthProfile(
                provider_user_id=str(user["id"]),
                login=user["login"],
                name=user.get("name"),
                avatar_url=user.get("avatar_url"),
            )
        except (KeyError, TypeError) as error:
            raise OAuthProviderUnavailable("GitHub вернул профиль без id или login") from error
        return OAuthLogin(profile=profile, grant=grant)

    def refresh(self, refresh_token: Secret) -> ProviderGrant:
        grant = self._grant(
            "обновление токена",
            {"grant_type": "refresh_token", "refresh_token": refresh_token.reveal()},
        )
        if grant == _BAD_REFRESH_TOKEN:
            raise ProviderTokenRejected("GitHub отверг refresh token")
        if isinstance(grant, str):
            raise OAuthProviderUnavailable(f"GitHub не обновил токен пользователя: {grant}")
        return grant

    def _grant(self, step: str, fields: dict[str, str]) -> ProviderGrant | str:
        """Токен из ответа `POST /login/oauth/access_token` или код `error` ответа."""
        body = self._json(
            step,
            lambda: self._client.post(
                _TOKEN_URL,
                headers={"Accept": "application/json"},
                data={"client_id": self._client_id, "client_secret": self._client_secret, **fields},
            ),
        )
        if not isinstance(body, dict):
            raise OAuthProviderUnavailable(f"GitHub ответил не объектом ({step})")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            # `error` — код вроде bad_verification_code, секретов в нём нет.
            # Строкой в сообщение — только код из известного алфавита.
            reason = body.get("error")
            return reason if isinstance(reason, str) and reason.isidentifier() else "без кода ошибки"
        refresh = body.get("refresh_token")
        expires_in = _seconds(body, "expires_in", step)
        refresh_expires_in = _seconds(body, "refresh_token_expires_in", step)
        if refresh is not None and (not isinstance(refresh, str) or not refresh):
            raise OAuthProviderUnavailable(f"GitHub вернул refresh_token не строкой ({step})")
        return ProviderGrant(
            access_token=Secret(token),
            expires_in=expires_in,
            refresh_token=Secret(refresh) if refresh else None,
            refresh_token_expires_in=refresh_expires_in if refresh else None,
        )

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


def _seconds(body: dict[str, Any], name: str, step: str) -> int | None:
    """Срок в секундах из ответа обмена: нет поля — без истечения."""
    value = body.get(name)
    if value is None:
        return None
    # bool — подкласс int, а `true` сроком не бывает.
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise OAuthProviderUnavailable(f"GitHub вернул {name} не положительным числом ({step})")
    return value
