"""Токен пользователя сессии: выдача, refresh, закрытие сессии. Фейки порта и unit of work."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.use_cases.user_token import (
    SessionClosed,
    call_with_user_token,
    revoke_quietly,
)
from app.domain.entities import ProviderToken, Secret
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_token import (
    ProviderTokenBusy,
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


def test_a_401_right_after_a_parallel_refresh_retries_with_the_new_token() -> None:
    """GitHub отверг старый access token, потому что параллельный запрос только что
    обменял refresh token: вызов повторяется один раз с новым, сессия жива."""
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))
    winner = token_from_grant(FakeOAuthProvider().refreshed, LOGIN)
    seen: list[Secret] = []

    def call(token: Secret) -> str:
        seen.append(token)
        if token == Secret("ghu_login_access"):
            uow.provider_tokens.save(SESSION_ID, winner)
            raise ProviderTokenRejected("401")
        return "ok"

    assert call_with_user_token(uow, FakeOAuthProvider(), SESSION_ID, LOGIN, call) == "ok"
    assert seen == [Secret("ghu_login_access"), Secret("ghu_refreshed_access")]
    assert [s.id for s in uow.sessions.stored] == [SESSION_ID]


def test_a_401_on_the_new_token_too_closes_the_session() -> None:
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))
    winner = token_from_grant(FakeOAuthProvider().refreshed, LOGIN)
    calls: list[Secret] = []

    def call(token: Secret) -> None:
        calls.append(token)
        uow.provider_tokens.save(SESSION_ID, winner)
        raise ProviderTokenRejected("401")

    with pytest.raises(SessionClosed):
        call_with_user_token(uow, FakeOAuthProvider(), SESSION_ID, LOGIN, call)
    assert len(calls) == 2
    assert uow.sessions.stored == []


def test_github_unavailable_on_refresh_keeps_using_a_token_that_is_still_valid() -> None:
    """Refresh — за 5 минут до истечения: пока access token действует, сбой
    обмена не превращается в `502`."""
    oauth = FakeOAuthProvider()
    oauth.refresh_error = OAuthProviderUnavailable("GitHub ответил 503 (обновление токена)")
    token = token_from_grant(EXPIRING_GRANT, LOGIN)
    uow = _uow(token)
    almost_expired = LOGIN + timedelta(hours=8) - timedelta(minutes=2)

    assert _seen(uow, oauth, almost_expired) == Secret("ghu_login_access")
    assert len(oauth.refreshes) == 1
    assert uow.provider_tokens.get(SESSION_ID) == token


def test_a_long_parallel_refresh_falls_back_to_a_still_valid_token() -> None:
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))

    def busy(session_id: UUID) -> ProviderToken | None:
        raise ProviderTokenBusy("токен сессии обновляет параллельный запрос")

    uow.provider_tokens.get_for_update = busy  # type: ignore[method-assign]
    almost_expired = LOGIN + timedelta(hours=8) - timedelta(minutes=2)

    assert _seen(uow, FakeOAuthProvider(), almost_expired) == Secret("ghu_login_access")
    with pytest.raises(OAuthProviderUnavailable):
        _seen(uow, FakeOAuthProvider(), LATER)
    assert [s.id for s in uow.sessions.stored] == [SESSION_ID]


def test_revoking_quietly_swallows_a_failure_without_logging_the_token(caplog: pytest.LogCaptureFixture) -> None:
    oauth = FakeOAuthProvider()
    oauth.revoke_error = OAuthProviderUnavailable("GitHub ответил 503 (отзыв токена)")

    with caplog.at_level(logging.DEBUG):
        revoke_quietly(oauth, Secret("ghu_login_access"))

    assert oauth.revoked == [Secret("ghu_login_access")]
    assert "не отозван" in caplog.text
    assert "ghu_login_access" not in caplog.text


def test_an_old_token_that_expires_mid_listing_is_refreshed_not_the_session_closed() -> None:
    """Refresh не удался, взят прежний токен; он истёк посреди обхода — `401`. Токен
    не сменился, но refresh token жив: обмен и один повтор, сессия жива."""
    oauth = FakeOAuthProvider()
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))
    seen: list[Secret] = []

    def call(token: Secret) -> str:
        seen.append(token)
        if token == Secret("ghu_login_access"):
            raise ProviderTokenRejected("401")
        return "ok"

    assert call_with_user_token(uow, oauth, SESSION_ID, LOGIN + timedelta(hours=1), call) == "ok"
    assert seen == [Secret("ghu_login_access"), Secret("ghu_refreshed_access")]
    assert oauth.refreshes == [Secret("ghr_login_refresh")]
    assert uow.provider_tokens.locked == [SESSION_ID]
    assert [s.id for s in uow.sessions.stored] == [SESSION_ID]


def test_a_401_on_a_token_that_cannot_be_refreshed_closes_the_session() -> None:
    oauth = FakeOAuthProvider()
    uow = _uow(ProviderToken(Secret("ghu_forever"), None, None, None))

    def reject(token: Secret) -> None:
        raise ProviderTokenRejected("401")

    with pytest.raises(SessionClosed):
        call_with_user_token(uow, oauth, SESSION_ID, LOGIN, reject)
    assert oauth.refreshes == []
    assert uow.sessions.stored == []


def test_a_token_with_less_than_a_minute_left_is_not_used_when_refresh_fails() -> None:
    oauth = FakeOAuthProvider()
    oauth.refresh_error = OAuthProviderUnavailable("GitHub ответил 503 (обновление токена)")
    uow = _uow(token_from_grant(EXPIRING_GRANT, LOGIN))
    last_minute = LOGIN + timedelta(hours=8) - timedelta(seconds=30)

    with pytest.raises(OAuthProviderUnavailable):
        _seen(uow, oauth, last_minute)
    assert [s.id for s in uow.sessions.stored] == [SESSION_ID]
