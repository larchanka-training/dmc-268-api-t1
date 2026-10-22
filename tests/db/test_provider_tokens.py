"""Токен пользователя при сессии на настоящей PostgreSQL: шифрование, каскад, блокировка, миграция."""

import datetime as dt
import os
import threading
import time
from uuid import UUID

import pytest
from sqlalchemy import inspect, text

from app.application.use_cases.user_token import call_with_user_token
from app.domain.entities import ProviderGrant, ProviderToken, Secret, User
from app.domain.enums import Provider
from app.domain.ids import new_id
from app.domain.provider_token import ProviderTokenUnavailable, token_from_grant
from app.domain.session import open_session
from app.infrastructure.db.token_cipher import TokenCipher
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = dt.datetime(2026, 10, 23, 8, 0, tzinfo=dt.UTC)
KEY = bytes(range(32))
GRANT = ProviderGrant(Secret("ghu_access-value"), 28_800, Secret("ghr_refresh-value"), 15_897_600)


def _uow(engine, key: bytes | None = KEY) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(engine, None if key is None else TokenCipher(key))


def _signed_in(engine, token: ProviderToken | None = None) -> UUID:
    """Пользователь, сессия и (если дан) её токен — одной транзакцией, как при входе."""
    account = str(new_id())
    user = User(new_id(), Provider.GITHUB, account, "octocat", None, None, NOW, NOW)
    session = open_session(new_id(), user.id, f"cookie-{account}", NOW)
    with _uow(engine) as work:
        work.users.add(user)
        work.sessions.add(session)
        if token is not None:
            work.provider_tokens.save(session.id, token)
        work.commit()
    return session.id


def test_a_token_round_trips_and_the_table_holds_only_ciphertext(clean_db) -> None:
    session_id = _signed_in(clean_db, token_from_grant(GRANT, NOW))

    with _uow(clean_db) as work:
        assert work.provider_tokens.get(session_id) == token_from_grant(GRANT, NOW)
    with clean_db.connect() as connection:
        row = connection.execute(text("SELECT * FROM provider_tokens")).mappings().one()
    dump = repr(dict(row)).encode() + bytes(row["access_token_encrypted"]) + bytes(row["refresh_token_encrypted"])
    assert b"ghu_access-value" not in dump
    assert b"ghr_refresh-value" not in dump


def test_a_token_without_expiry_round_trips(clean_db) -> None:
    forever = ProviderToken(Secret("ghu_forever"), None, None, None)
    session_id = _signed_in(clean_db, forever)

    with _uow(clean_db) as work:
        assert work.provider_tokens.get(session_id) == forever


def test_save_replaces_the_token_of_the_session(clean_db) -> None:
    session_id = _signed_in(clean_db, token_from_grant(GRANT, NOW))
    fresh = token_from_grant(ProviderGrant(Secret("ghu_new"), 28_800, Secret("ghr_new"), 100), NOW)

    with _uow(clean_db) as work:
        assert work.provider_tokens.get_for_update(session_id) is not None
        work.provider_tokens.save(session_id, fresh)
        work.commit()
    with _uow(clean_db) as work:
        assert work.provider_tokens.get(session_id) == fresh


def test_another_key_or_no_key_cannot_read_and_no_key_cannot_write(clean_db) -> None:
    session_id = _signed_in(clean_db, token_from_grant(GRANT, NOW))

    with _uow(clean_db, os.urandom(32)) as work, pytest.raises(ProviderTokenUnavailable):
        work.provider_tokens.get(session_id)
    with _uow(clean_db, None) as work, pytest.raises(ProviderTokenUnavailable):
        work.provider_tokens.get(session_id)
    with _uow(clean_db, None) as work, pytest.raises(ProviderTokenUnavailable):
        work.provider_tokens.save(session_id, token_from_grant(GRANT, NOW))


def test_logout_and_purge_delete_the_token_with_the_session(clean_db) -> None:
    logged_out = _signed_in(clean_db, token_from_grant(GRANT, NOW))
    _signed_in(clean_db, token_from_grant(GRANT, NOW))

    with _uow(clean_db) as work:
        work.sessions.delete(logged_out)
        work.commit()
    with _uow(clean_db) as work:
        # Сессии живут 30 дней от входа.
        assert work.sessions.delete_expired(NOW + dt.timedelta(days=31)) == 1
        work.commit()

    with clean_db.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM provider_tokens")).scalar_one() == 0


class _SlowRefresher:
    """Провайдер, обмен которого идёт заметное время: окно для гонки."""

    provider = Provider.GITHUB

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def refresh(self, refresh_token: Secret) -> ProviderGrant:
        with self._lock:
            self.calls += 1
        time.sleep(0.5)
        return ProviderGrant(Secret("ghu_refreshed"), 28_800, Secret("ghr_refreshed"), 15_897_600)


def test_two_parallel_refreshes_of_one_session_spend_the_refresh_token_once(clean_db) -> None:
    """Design D4: блокировка строки токена — второй запрос ждёт и берёт уже обновлённый."""
    session_id = _signed_in(clean_db, token_from_grant(GRANT, NOW))
    later = NOW + dt.timedelta(hours=9)
    refresher = _SlowRefresher()
    seen: list[str] = []
    errors: list[BaseException] = []

    def request() -> None:
        try:
            token = call_with_user_token(_uow(clean_db), refresher, session_id, later, lambda t: t)  # type: ignore[arg-type]
            seen.append(token.reveal())
        except BaseException as error:  # noqa: BLE001 - поток сообщает об ошибке тесту
            errors.append(error)

    threads = [threading.Thread(target=request) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert errors == []
    assert refresher.calls == 1
    assert seen == ["ghu_refreshed", "ghu_refreshed"]


def test_migration_0007_downgrades_and_upgrades_cleanly(migrated) -> None:
    from alembic.config import Config

    from alembic import command

    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    try:
        command.downgrade(config, "0006")
        assert "provider_tokens" not in inspect(migrated).get_table_names()
        assert "sessions" in inspect(migrated).get_table_names()
    finally:
        command.upgrade(config, "head")
    assert "provider_tokens" in inspect(migrated).get_table_names()
