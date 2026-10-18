import datetime as dt
import hashlib
from uuid import UUID

from app.domain.session import SESSION_LIFETIME, hash_token, is_expired, open_session

NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.UTC)
USER_ID = UUID("01920000-0000-7000-8000-000000000001")
SESSION_ID = UUID("01920000-0000-7000-8000-000000000002")


def test_a_session_lives_thirty_days_from_login() -> None:
    session = open_session(SESSION_ID, USER_ID, "token", NOW)

    assert SESSION_LIFETIME == dt.timedelta(days=30)
    assert session.created_at == NOW
    assert session.expires_at == NOW + dt.timedelta(days=30)


def test_a_session_stores_the_hash_not_the_token() -> None:
    session = open_session(SESSION_ID, USER_ID, "token", NOW)

    assert session.token_hash == hashlib.sha256(b"token").digest()
    assert session.token_hash == hash_token("token")


def test_a_session_is_valid_until_its_expiry_and_not_at_it() -> None:
    session = open_session(SESSION_ID, USER_ID, "token", NOW)

    assert not is_expired(session, session.expires_at - dt.timedelta(microseconds=1))
    assert is_expired(session, session.expires_at)
