import datetime as dt
from dataclasses import replace

import pytest
from sqlalchemy import text

from app.domain.entities import User
from app.domain.enums import Provider
from app.domain.ids import new_id
from app.domain.session import hash_token, open_session
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def uow(clean_db):
    return SqlAlchemyUnitOfWork(clean_db)


def a_user() -> User:
    return User(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_user_id="583231",
        login="octocat",
        name="The Octocat",
        avatar_url="https://avatars.githubusercontent.com/u/583231",
        created_at=NOW,
        updated_at=NOW,
    )


def test_a_user_is_found_by_provider_account_and_updated(uow) -> None:
    user = a_user()
    with uow as work:
        work.users.add(user)
        work.commit()
    with uow as work:
        work.users.update(replace(user, login="octo", name=None))
        work.commit()
    with uow as work:
        stored = work.users.find_by_provider(Provider.GITHUB, "583231")
    assert stored is not None
    assert (stored.id, stored.login, stored.name) == (user.id, "octo", None)


def test_a_user_and_their_session_are_written_in_one_transaction(uow) -> None:
    user = a_user()
    session = open_session(new_id(), user.id, "token", NOW)
    with uow as work:
        work.sessions.add(session)
        work.users.add(user)
        work.commit()
    with uow as work:
        assert work.sessions.find_by_token_hash(hash_token("token")) == session


def test_the_token_itself_is_not_stored(uow, clean_db) -> None:
    user = a_user()
    with uow as work:
        work.users.add(user)
        work.sessions.add(open_session(new_id(), user.id, "token-value", NOW))
        work.commit()
    with clean_db.connect() as connection:
        rows = connection.execute(text("SELECT token_hash FROM sessions")).all()
    assert rows == [(hash_token("token-value"),)]
    assert b"token-value" not in rows[0][0]


def test_a_deleted_session_is_gone_and_deleting_twice_is_fine(uow) -> None:
    user = a_user()
    session = open_session(new_id(), user.id, "token", NOW)
    with uow as work:
        work.users.add(user)
        work.sessions.add(session)
        work.commit()
    with uow as work:
        work.sessions.delete(session.id)
        work.sessions.delete(session.id)
        work.commit()
    with uow as work:
        assert work.sessions.find_by_token_hash(session.token_hash) is None


def test_deleting_a_user_deletes_their_sessions(uow, clean_db) -> None:
    user = a_user()
    session = open_session(new_id(), user.id, "token", NOW)
    with uow as work:
        work.users.add(user)
        work.sessions.add(session)
        work.commit()
    with clean_db.begin() as connection:
        connection.execute(text("DELETE FROM users"))
    with uow as work:
        assert work.sessions.find_by_token_hash(session.token_hash) is None
