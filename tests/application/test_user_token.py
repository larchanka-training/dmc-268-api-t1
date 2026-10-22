"""Токен пользователя сессии: выдача, refresh, закрытие сессии. Фейки порта и unit of work."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.use_cases.user_token import SessionClosed, call_with_user_token
from app.domain.entities import ProviderToken, Secret
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_token import (
    ProviderTokenRejected,
    ProviderTokenUnavailable,
    token_from_grant,
)
from app.domain.session import open_session

from ..fakes.oauth import EXPIRING_GRANT, FakeOAuthProvider, rejecting
from ..fakes.unit_of_work import FakeUnitOfWork

LOGIN = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
# Через 9 часов после входа access token (8 часов) истёк, refresh token жив.
LATER = LOGIN + timedelta(hours=9)
SESSION_ID = UUID(int=0x5E)


def _uow(token: ProviderToken | None = None) -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    uow.sessions.add(open_session(SESSION_ID, UUID(int=1), "cookie", LOGIN))
    if token is not None:
        uow.provider_tokens.save(SESSION_ID, token)
    return uow


def _seen(uow: FakeUnitOfWork, oauth: FakeOAuthProvider, now: datetime) -> Secret:
    return call_with_user_token(uow, oauth, SESSION_ID, now, lambda token: token)


def test_a_fresh_token_is_used_as_is() -> None:
    oauth = FakeOAuthProvider()
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))

    assert _seen(uow, oauth, LOGIN + timedelta(hours=1)) == Secret("ghu_login_access")
    assert oauth.refreshes == []


def test_an_expired_token_is_refreshed_and_the_new_pair_is_saved() -> None:
    oauth = FakeOAuthProvider()
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))

    assert _seen(uow, oauth, LATER) == Secret("ghu_refreshed_access")

    assert oauth.refreshes == [Secret("ghr_login_refresh")]
    assert uow.provider_tokens.locked == [SESSION_ID]
    stored = uow.provider_tokens.get(SESSION_ID)
    assert stored == token_from_grant(oauth.refreshed, LATER)


def test_a_token_without_expiry_is_never_refreshed() -> None:
    oauth = FakeOAuthProvider()
    forever = ProviderToken(Secret("ghu_forever"), None, None, None)
    uow = _uow(forever)

    assert _seen(uow, oauth, LOGIN + timedelta(days=365)) == Secret("ghu_forever")
    assert oauth.refreshes == []


def test_a_rejected_refresh_closes_the_session() -> None:
    oauth = FakeOAuthProvider()
    oauth.refresh_error = rejecting()
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))

    with pytest.raises(SessionClosed):
        _seen(uow, oauth, LATER)

    assert uow.sessions.stored == []
    assert uow.provider_tokens.stored == {}


def test_an_expired_refresh_token_closes_the_session_without_asking_github() -> None:
    oauth = FakeOAuthProvider()
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))

    with pytest.raises(SessionClosed):
        _seen(uow, oauth, LOGIN + timedelta(days=200))

    assert oauth.refreshes == []
    assert uow.sessions.stored == []


def test_github_unavailable_on_refresh_keeps_the_session_and_the_old_token() -> None:
    oauth = FakeOAuthProvider()
    oauth.refresh_error = OAuthProviderUnavailable("GitHub ответил 503 (обновление токена)")
    token = token_from_grant(EXPIRING_GRANT, LOGIN)
    uow = _uow(token)

    with pytest.raises(OAuthProviderUnavailable):
        _seen(uow, oauth, LATER)

    assert [s.id for s in uow.sessions.stored] == [SESSION_ID]
    assert uow.provider_tokens.get(SESSION_ID) == token


def test_a_session_opened_before_tokens_were_stored_is_closed() -> None:
    uow = _uow(None)

    with pytest.raises(SessionClosed):
        _seen(uow, FakeOAuthProvider(), LOGIN)

    assert uow.sessions.stored == []


def test_an_unreadable_token_closes_the_session() -> None:
    """Ключ сменили или не задали: запись не расшифровывается — новый вход."""
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))

    def unreadable(session_id: UUID) -> ProviderToken | None:
        raise ProviderTokenUnavailable("токен не расшифровывается: другой ключ или чужая запись")

    uow.provider_tokens.get = unreadable  # type: ignore[method-assign]
    with pytest.raises(SessionClosed):
        _seen(uow, FakeOAuthProvider(), LOGIN)
    assert uow.sessions.stored == []


def test_a_parallel_refresh_that_won_is_reused_not_repeated() -> None:
    """Гонка двух запросов одной сессии: первый чтение видит истёкший токен, но
    к моменту блокировки другой запрос уже обменял refresh token. Второго обмена
    нет — одноразовый refresh token не тратится дважды (design D4)."""
    oauth = FakeOAuthProvider()
    stale = token_from_grant(EXPIRING_GRANT, LOGIN)
    uow = _uow(stale)
    winner = token_from_grant(oauth.refreshed, LATER)
    original_lock = uow.provider_tokens.get_for_update

    def lock_after_the_winner(session_id: UUID) -> ProviderToken | None:
        uow.provider_tokens.save(session_id, winner)
        return original_lock(session_id)

    uow.provider_tokens.get_for_update = lock_after_the_winner  # type: ignore[method-assign]

    assert _seen(uow, oauth, LATER) == Secret("ghu_refreshed_access")
    assert oauth.refreshes == []


def test_a_rejected_access_token_closes_the_session() -> None:
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))

    def reject(token: Secret) -> None:
        raise ProviderTokenRejected("401")

    with pytest.raises(SessionClosed):
        call_with_user_token(uow, FakeOAuthProvider(), SESSION_ID, LOGIN, reject)
    assert uow.sessions.stored == []


def test_tokens_stay_out_of_the_log(caplog: pytest.LogCaptureFixture) -> None:
    oauth = FakeOAuthProvider()
    oauth.refresh_error = rejecting()
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))

    with caplog.at_level(logging.DEBUG), pytest.raises(SessionClosed):
        _seen(uow, oauth, LATER)

    assert "сессия закрыта" in caplog.text
    for value in ("ghu_login_access", "ghr_login_refresh", "ghu_refreshed_access"):
        assert value not in caplog.text
