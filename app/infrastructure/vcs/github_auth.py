"""Аутентификация GitHub App: короткоживущий JWT и installation tokens.

JWT подписывается RS256 приватным ключом приложения (iss = App ID, окно жизни
≤ 10 минут — максимум по документации GitHub Apps) и обменивается на
installation token per `installation_id`. Токены кэшируются в dict с expiry
внутри адаптера: порт CacheStore не вводится, пока сервис — одна реплика
(design `add-webhook-intake-and-vcs-gateway`, D7).
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from app.infrastructure.vcs.errors import VcsAuthError, VcsUnavailableError

# Документация GitHub Apps: exp не дальше 10 минут от iat.
_JWT_TTL_SECONDS = 10 * 60
# Запас до истечения installation token: токен, которому жить меньше минуты,
# считаем просроченным, чтобы не выиграть гонку с часами GitHub.
_EXPIRY_MARGIN = timedelta(seconds=60)


class GitHubAppAuth:
    """Выдаёт installation tokens, кэшируя их до истечения."""

    def __init__(self, app_id: str, private_key: str, client: httpx.Client) -> None:
        """Клиент приходит снаружи (в тестах — с MockTransport); сети здесь нет."""
        self._app_id = app_id
        self._private_key = private_key
        self._client = client
        self._cache: dict[int, tuple[str, datetime]] = {}

    def installation_token(self, installation_id: int) -> str:
        """Токен для инсталляции; отсутствующий или просроченный — новый запрос."""
        cached = self._cache.get(installation_id)
        if cached is not None and not self._needs_refresh(cached[1]):
            return cached[0]
        token, expires_at = self._fetch(installation_id)
        self._cache[installation_id] = (token, expires_at)
        return token

    def invalidate(self, installation_id: int) -> None:
        """Забыть кэшированный токен: после 401 он мог быть отозван досрочно
        (приостановка App, переустановка, ротация ключа) — тогда держать его
        до конца TTL значило бы отвечать 502 на каждый вебхук инсталляции."""
        self._cache.pop(installation_id, None)

    def _needs_refresh(self, expires_at: datetime) -> bool:
        return expires_at - _EXPIRY_MARGIN <= datetime.now(UTC)

    def _fetch(self, installation_id: int) -> tuple[str, datetime]:
        try:
            response = self._client.post(
                f"/app/installations/{installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {self._build_jwt()}"},
            )
        except httpx.RequestError as error:
            raise VcsUnavailableError(
                f"GitHub недоступен при запросе installation token"
                f" для {installation_id}: {error}"
            ) from error
        if response.is_error:
            raise VcsAuthError(
                "GitHub не выдал installation token"
                f" для {installation_id}: HTTP {response.status_code}"
            )
        try:
            payload: dict[str, Any] = response.json()
            token: str = payload["token"]
            return token, datetime.fromisoformat(payload["expires_at"])
        except (ValueError, KeyError, TypeError) as error:
            raise VcsAuthError(
                f"GitHub вернул неожиданное тело на выдаче token"
                f" для {installation_id}: {error!r}"
            ) from error

    def _build_jwt(self) -> str:
        issued_at = int(time.time())
        return jwt.encode(
            {
                "iss": self._app_id,
                "iat": issued_at,
                "exp": issued_at + _JWT_TTL_SECONDS,
            },
            self._private_key,
            algorithm="RS256",
        )
