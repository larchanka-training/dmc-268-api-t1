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
проходит наверх как есть: сессия не виновата, и её не закрываем.

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
from app.domain.provider_token import (
    ProviderTokenRejected,
    ProviderTokenUnavailable,
    can_refresh,
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

    Отказ провайдера в токене (`ProviderTokenRejected` из `call`) закрывает
    сессию так же, как отсутствующий или необновляемый токен.
    """
    access_token = _access_token(uow, oauth, session_id, now)
    try:
        return call(access_token)
    except ProviderTokenRejected:
        _close(uow, session_id, "провайдер отверг токен пользователя")
        raise SessionClosed from None


def _access_token(uow: UnitOfWork, oauth: OAuthProvider, session_id: UUID, now: datetime) -> Secret:
    try:
        with uow as work:
            token = work.provider_tokens.get(session_id)
        if token is not None and not needs_refresh(token, now):
            return token.access_token
        if token is None:
            _close(uow, session_id, "у сессии нет токена провайдера (открыта до его хранения)")
            raise SessionClosed
        return _refreshed(uow, oauth, session_id, now)
    except ProviderTokenUnavailable as failure:
        _close(uow, session_id, f"токен провайдера не читается: {failure}")
        raise SessionClosed from None


def _refreshed(uow: UnitOfWork, oauth: OAuthProvider, session_id: UUID, now: datetime) -> Secret:
    """Обменять refresh token, держа запись токена до коммита (design D4)."""
    with uow as work:
        token = work.provider_tokens.get_for_update(session_id)
        if token is not None and not needs_refresh(token, now):
            # Параллельный запрос той же сессии обновил токен, пока мы ждали.
            return token.access_token
        if token is None or token.refresh_token is None or not can_refresh(token, now):
            reason = "токен провайдера истёк и не обновляется"
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
