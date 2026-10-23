"""Токен пользователя у провайдера для запроса от имени сессии.

Токен хранится при сессии (`ProviderTokenRepo`); истёкший обменивается по
refresh token через порт `OAuthProvider`. Когда обновлять и можно ли —
решают чистые функции `app/domain/provider_token.py`, время приходит
аргументом.

Токен, который нельзя использовать, — нет записи (сессия открыта до
change `repositories-via-user-token`), запись под другим ключом шифрования,
refresh token отвергнут или истёк, провайдер ответил `401` на токен, —
закрывает сессию: вызывающий получает `SessionClosed` и отвечает `401`, а
пользователь входит заново. Сбой провайдера (`OAuthProviderUnavailable`)
при обновлении ещё действующего access token не мешает — используется
прежний; при истёкшем — проходит наверх как есть: сессия не виновата, и её
не закрываем.

Гонка двух refresh одной сессии (refresh token одноразовый) снята
блокировкой записи токена на время обмена: второй запрос ждёт первого и
видит уже обновлённый токен (design D4).
"""

import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.application.ports.oauth_provider import OAuthProvider
from app.application.ports.unit_of_work import UnitOfWork
from app.domain.entities import Secret
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_token import (
    ProviderTokenRejected,
    ProviderTokenUnavailable,
    can_refresh,
    is_usable,
    needs_refresh,
    token_from_grant,
)

_log = logging.getLogger(__name__)


class SessionClosed(RuntimeError):
    """Токен сессии непригоден, сессия закрыта: нужен новый вход."""


def call_with_user_token[T](
    uow: UnitOfWork,
    oauth: OAuthProvider,
    session_id: UUID,
    now: datetime,
    call: Callable[[Secret], T],
) -> T:
    """Вызвать `call` с действующим токеном пользователя сессии.

    Отказ провайдера в токене (`ProviderTokenRejected` из `call`) — не сразу
    закрытие сессии. Под блокировкой записи токена: параллельный запрос уже
    сменил токен — берём его; не сменил, но refresh token жив — обмениваем.
    Затем вызов повторяется один раз. Закрывается сессия, только если обновить
    нечем или повтор тоже отвергнут (design D1 change `user-token-listing-edges`).
    """
    access_token = _access_token(uow, oauth, session_id, now)
    try:
        return call(access_token)
    except ProviderTokenRejected:
        pass
    retry_token = _guarded(uow, session_id, lambda: _refreshed(uow, oauth, session_id, now, rejected=access_token))
    try:
        return call(retry_token)
    except ProviderTokenRejected:
        _close(uow, session_id, "провайдер отверг токен пользователя и после обновления")
        raise SessionClosed from None


def revoke_quietly(oauth: OAuthProvider, access_token: Secret) -> None:
    """Отозвать токен у провайдера после выхода; сбой не мешает выходу.

    Best-effort: сессия и токен в базе уже удалены, отзыв лишь сокращает жизнь
    токена у провайдера. В лог — только факт сбоя, без токена.
    """
    try:
        oauth.revoke(access_token)
    except OAuthProviderUnavailable as failure:
        _log.warning("токен провайдера не отозван при выходе: %s", failure)


def _guarded[T](uow: UnitOfWork, session_id: UUID, read: Callable[[], T]) -> T:
    """`read` с закрытием сессии, если токен не читается (нет ключа, другой ключ)."""
    try:
        return read()
    except ProviderTokenUnavailable as failure:
        _close(uow, session_id, f"токен провайдера не читается: {failure}")
        raise SessionClosed from None


def _access_token(uow: UnitOfWork, oauth: OAuthProvider, session_id: UUID, now: datetime) -> Secret:
    return _guarded(uow, session_id, lambda: _fresh_or_refreshed(uow, oauth, session_id, now))


def _fresh_or_refreshed(uow: UnitOfWork, oauth: OAuthProvider, session_id: UUID, now: datetime) -> Secret:
    with uow as work:
        token = work.provider_tokens.get(session_id)
    if token is not None and not needs_refresh(token, now):
        return token.access_token
    if token is None:
        _close(uow, session_id, "у сессии нет токена провайдера (открыта до его хранения)")
        raise SessionClosed
    try:
        return _refreshed(uow, oauth, session_id, now)
    except OAuthProviderUnavailable:
        # Обновляем заранее, за запас до истечения: пока access token
        # поживёт ещё минуту, временный сбой GitHub или долгая чужая блокировка
        # не повод отвечать `502` (design D2 change `user-token-hardening`).
        # Истечёт посреди обхода — `401`, и `call_with_user_token` обновит снова.
        if is_usable(token, now):
            _log.warning("токен провайдера не обновлён, используется прежний до его истечения")
            return token.access_token
        raise


def _refreshed(
    uow: UnitOfWork, oauth: OAuthProvider, session_id: UUID, now: datetime, rejected: Secret | None = None
) -> Secret:
    """Обменять refresh token, держа запись токена до коммита (design D4).

    `rejected` — access token, который провайдер только что отверг: тогда
    обмен нужен, если токен в записи всё ещё он, независимо от срока.
    Чужая блокировка дольше предела — `ProviderTokenBusy` из `get_for_update`.
    """
    with uow as work:
        token = work.provider_tokens.get_for_update(session_id)
        if token is not None and (
            token.access_token != rejected if rejected is not None else not needs_refresh(token, now)
        ):
            # Параллельный запрос той же сессии обновил токен, пока мы ждали.
            return token.access_token
        if token is None or token.refresh_token is None or not can_refresh(token, now):
            reason = "токен провайдера не действует и не обновляется"
        else:
            try:
                grant = oauth.refresh(token.refresh_token)
            except ProviderTokenRejected:
                reason = "провайдер отверг refresh token"
            else:
                fresh = token_from_grant(grant, now)
                work.provider_tokens.save(session_id, fresh)
                work.commit()
                return fresh.access_token
    _close(uow, session_id, reason)
    raise SessionClosed


def _close(uow: UnitOfWork, session_id: UUID, reason: str) -> None:
    # В сообщении причина без значений токенов: по ней видно на стенде, почему
    # пользователя попросили войти заново.
    _log.info("сессия закрыта: %s", reason)
    with uow as work:
        work.sessions.delete(session_id)
        work.commit()
