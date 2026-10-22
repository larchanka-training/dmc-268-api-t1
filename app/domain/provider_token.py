"""Токен пользователя у провайдера: сроки, обновление и отказы.

Чистые функции: время приходит аргументом. Когда обновлять токен, можно ли
его обновить и какие сроки у выданного — решается здесь; HTTP-обмен — дело
адаптера провайдера входа, хранение и шифрование — адаптера базы
(change `repositories-via-user-token`).
"""

from datetime import datetime, timedelta

from app.domain.entities import ProviderGrant, ProviderToken
from app.domain.login import OAuthProviderUnavailable

# Обновляем заранее: список репозиториев — несколько запросов подряд, и токен,
# истекающий посреди них, дал бы `401` на полпути.
REFRESH_MARGIN = timedelta(minutes=5)
# Прежний токен берём при сбое обновления, только если до истечения больше
# минуты: иначе он истечёт посреди обхода страниц.
USABLE_MARGIN = timedelta(seconds=60)


class ProviderTokenRejected(RuntimeError):
    """Провайдер отверг токен пользователя или refresh token: нужен новый вход.

    В домене, как `OAuthProviderUnavailable`: адаптеры её бросают, use case
    ловит и закрывает сессию, и ни один не импортирует другого.
    """


class ProviderTokenUnavailable(RuntimeError):
    """Токен нельзя записать или прочитать: не задан ключ шифрования или
    запись зашифрована другим ключом. Значения токена в сообщении нет."""


class ProviderTokenBusy(OAuthProviderUnavailable):
    """Запись токена держит параллельный refresh дольше предела ожидания блокировки.

    Наследник `OAuthProviderUnavailable`: блокировку держит запрос, который
    ждёт GitHub, — для вызывающего это тот же сбой провайдера (`502`).
    """


def token_from_grant(grant: ProviderGrant, now: datetime) -> ProviderToken:
    """Токен с абсолютными сроками из ответа провайдера, полученного в момент `now`."""
    return ProviderToken(
        access_token=grant.access_token,
        access_expires_at=_after(now, grant.expires_in),
        refresh_token=grant.refresh_token,
        refresh_expires_at=_after(now, grant.refresh_token_expires_in) if grant.refresh_token else None,
    )


def needs_refresh(token: ProviderToken, now: datetime) -> bool:
    """Пора ли обменять refresh token: access token истёк или истечёт в пределах запаса."""
    return token.access_expires_at is not None and now >= token.access_expires_at - REFRESH_MARGIN


def is_usable(token: ProviderToken, now: datetime) -> bool:
    """Access token поживёт ещё хотя бы `USABLE_MARGIN`: можно идти с ним, если обновить не вышло."""
    return token.access_expires_at is None or now < token.access_expires_at - USABLE_MARGIN


def can_refresh(token: ProviderToken, now: datetime) -> bool:
    """Есть ли действующий refresh token."""
    if token.refresh_token is None:
        return False
    return token.refresh_expires_at is None or now < token.refresh_expires_at


def _after(now: datetime, seconds: int | None) -> datetime | None:
    return None if seconds is None else now + timedelta(seconds=seconds)
