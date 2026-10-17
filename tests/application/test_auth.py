import datetime as dt
from dataclasses import replace
from uuid import UUID

import pytest

from app.application.use_cases.auth import (
    LoginFailed,
    LoginStart,
    LoginSucceeded,
    authenticate,
    complete_login,
    logout,
    start_login,
)
from app.domain.entities import User
from app.domain.enums import Provider
from app.domain.login import LoginError
from app.domain.session import hash_token

from ..fakes.oauth import OCTOCAT, FakeOAuthProvider
from ..fakes.unit_of_work import FakeUnitOfWork, FakeUserRepo

NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.UTC)
LATER = NOW + dt.timedelta(days=1)


def ids():
    counter = iter(range(1, 100))
    return lambda: UUID(int=next(counter))


def login(uow, provider, *, token="cookie-1", now=NOW, new_id=None, **overrides):
    arguments = {
        "state": "s-1",
        "expected_state": "s-1",
        "code": "code-1",
        "error": None,
        "redirect": "/app/reviews/42",
    } | overrides
    return complete_login(
        uow, provider, token=token, now=now, new_id=new_id or ids(), **arguments
    )


class LosingRaceUserRepo(FakeUserRepo):
    """`add` проигрывает гонку: параллельный первый вход успел вставить строку раньше."""

    def __init__(self, twin: User | None) -> None:
        super().__init__()
        self.twin = twin

    def add(self, user: User) -> bool:
        if self.twin is not None:
            self._by_id[self.twin.id] = self.twin
        return False


def _uow_losing_the_race(twin: User | None) -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    repo = LosingRaceUserRepo(twin)
    uow.users = repo
    uow._fakes.users = repo  # снимки транзакции берутся из общего набора фейков
    return uow


def test_start_login_points_at_the_provider_with_the_state_and_a_checked_redirect() -> None:
    start = start_login(FakeOAuthProvider(), "//evil.example/app/", "s-1")
    assert isinstance(start, LoginStart)

    assert start.authorize_url == "https://github.com/login/oauth/authorize?state=s-1"
    assert (start.state, start.redirect) == ("s-1", "/app/")


def test_first_login_creates_the_user_and_a_session() -> None:
    uow, provider = FakeUnitOfWork(), FakeOAuthProvider()

    result = login(uow, provider)

    assert result == LoginSucceeded(token="cookie-1", redirect="/app/reviews/42")
    user = uow.users.find_by_provider(Provider.GITHUB, "583231")
    assert user is not None
    assert (user.login, user.created_at) == ("octocat", NOW)
    [session] = uow.sessions.stored
    assert (session.user_id, session.token_hash) == (user.id, hash_token("cookie-1"))
    assert session.expires_at == NOW + dt.timedelta(days=30)
    assert provider.exchanged == ["code-1"]


def test_second_login_updates_the_profile_and_opens_another_session() -> None:
    uow, provider = FakeUnitOfWork(), FakeOAuthProvider()
    login(uow, provider)
    first = uow.users.find_by_provider(Provider.GITHUB, "583231")
    provider.profile = replace(OCTOCAT, login="octo", avatar_url=None)

    login(uow, provider, token="cookie-2", now=LATER, new_id=lambda: UUID(int=500))

    user = uow.users.find_by_provider(Provider.GITHUB, "583231")
    assert user is not None and first is not None
    assert (user.id, user.login, user.avatar_url) == (first.id, "octo", None)
    assert len(uow.sessions.stored) == 2


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"state": "other"}, LoginError.STATE_MISMATCH),
        ({"expected_state": None}, LoginError.STATE_MISMATCH),
        ({"state": None}, LoginError.STATE_MISMATCH),
        ({"code": None, "error": "access_denied"}, LoginError.DENIED),
        ({"code": None, "error": "server_error"}, LoginError.PROVIDER_UNAVAILABLE),
        ({"code": None}, LoginError.PROVIDER_UNAVAILABLE),
    ],
)
def test_a_failed_callback_creates_nothing(overrides, error) -> None:
    uow, provider = FakeUnitOfWork(), FakeOAuthProvider()

    assert login(uow, provider, **overrides) == LoginFailed(error)
    assert uow.sessions.stored == []
    assert provider.exchanged == []


def test_an_unavailable_provider_creates_nothing() -> None:
    uow, provider = FakeUnitOfWork(), FakeOAuthProvider()
    provider.unavailable = True

    assert login(uow, provider) == LoginFailed(LoginError.PROVIDER_UNAVAILABLE)
    assert uow.users.find_by_provider(Provider.GITHUB, "583231") is None
    assert uow.sessions.stored == []


def test_the_redirect_from_the_cookie_is_checked_again() -> None:
    result = login(FakeUnitOfWork(), FakeOAuthProvider(), redirect="https://evil.example/")

    assert result == LoginSucceeded(token="cookie-1", redirect="/app/")


def test_a_live_session_authenticates_its_user() -> None:
    uow = FakeUnitOfWork()
    login(uow, FakeOAuthProvider())

    user = authenticate(uow, "cookie-1", LATER)

    assert user is not None and user.login == "octocat"


@pytest.mark.parametrize("token", [None, "", "unknown"])
def test_no_or_unknown_cookie_authenticates_nobody(token) -> None:
    uow = FakeUnitOfWork()
    login(uow, FakeOAuthProvider())

    assert authenticate(uow, token, LATER) is None


def test_an_expired_session_is_refused_and_deleted() -> None:
    uow = FakeUnitOfWork()
    login(uow, FakeOAuthProvider())

    assert authenticate(uow, "cookie-1", NOW + dt.timedelta(days=30)) is None
    assert uow.sessions.stored == []


def test_logout_ends_the_session_for_every_tab_and_is_idempotent() -> None:
    uow = FakeUnitOfWork()
    login(uow, FakeOAuthProvider())

    logout(uow, "cookie-1")
    logout(uow, "cookie-1")
    logout(uow, None)

    assert authenticate(uow, "cookie-1", LATER) is None


def test_a_new_login_revokes_the_session_of_the_cookie_it_came_with() -> None:
    uow = FakeUnitOfWork()
    provider = FakeOAuthProvider()
    login(uow, provider, token="old-cookie")
    [old] = uow.sessions.stored

    login(uow, provider, token="new-cookie", previous_token="old-cookie")

    assert old.id not in {s.id for s in uow.sessions.stored}
    assert len(uow.sessions.stored) == 1


def test_a_stale_previous_cookie_does_not_break_the_login() -> None:
    uow = FakeUnitOfWork()

    result = login(uow, FakeOAuthProvider(), previous_token="never-issued")

    assert isinstance(result, LoginSucceeded)
    assert len(uow.sessions.stored) == 1


def test_a_lost_insert_race_refreshes_the_winners_row_instead_of_creating_a_second() -> None:
    twin = User(
        id=UUID(int=777),
        provider=Provider.GITHUB,
        provider_user_id="583231",
        login="old-login",
        name="Old Name",
        avatar_url="https://example.test/old.png",
        created_at=NOW - dt.timedelta(seconds=1),
        updated_at=NOW - dt.timedelta(seconds=1),
    )
    uow = _uow_losing_the_race(twin)

    result = login(uow, FakeOAuthProvider())

    assert result == LoginSucceeded(token="cookie-1", redirect="/app/reviews/42")
    [stored] = uow.users._by_id.values()
    assert stored.id == twin.id
    assert (stored.login, stored.name, stored.avatar_url) == (
        OCTOCAT.login,
        OCTOCAT.name,
        OCTOCAT.avatar_url,
    )
    [session] = uow.sessions.stored
    assert session.user_id == twin.id


def test_a_lost_insert_race_with_no_row_to_find_is_an_error_and_creates_nothing() -> None:
    uow = _uow_losing_the_race(None)

    with pytest.raises(RuntimeError, match="не вставлен и не найден"):
        login(uow, FakeOAuthProvider())

    assert uow.sessions.stored == []
