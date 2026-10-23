"""Вошедший пользователь для тестов ручек, которым сессия не предмет проверки."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI

from app.api.dependencies import get_unit_of_work_factory
from app.api.session import SESSION_COOKIE, current_session, current_user
from app.application.use_cases.auth import SignedIn
from app.domain.entities import User
from app.domain.enums import Provider
from app.domain.provider_token import token_from_grant
from app.domain.session import open_session

from .oauth import EXPIRING_GRANT
from .unit_of_work import FakeUnitOfWork

SIGNED_IN = User(
    id=UUID("01920000-0000-7000-8000-0000000000aa"),
    provider=Provider.GITHUB,
    provider_user_id="583231",
    login="octocat",
    name="The Octocat",
    avatar_url=None,
    created_at=datetime(2026, 10, 1, tzinfo=UTC),
    updated_at=datetime(2026, 10, 1, tzinfo=UTC),
)


SIGNED_IN_SESSION_ID = UUID(int=0xA5)


def sign_in(app: FastAPI, user: User = SIGNED_IN) -> None:
    """Подменить проверку сессии: запросы идут от `user` из сессии `SIGNED_IN_SESSION_ID`."""
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[current_session] = lambda: SignedIn(user=user, session_id=SIGNED_IN_SESSION_ID)


SESSION_TOKEN = "signed-in-session"


def sign_in_with_session(
    app: FastAPI, uow: FakeUnitOfWork, *, with_token: bool = True
) -> dict[str, str]:
    """Вошедший пользователь с живой сессией в `uow` — для ручек, которые
    перечитывают сессию сами, как поток SSE. Сессия с токеном провайдера, как после
    настоящего входа: без токена сессия недействительна. `with_token=False` — сессия,
    открытая до хранения токенов. Возвращает cookie для клиента."""
    sign_in(app)
    now = datetime.now(UTC)
    uow.users.add(SIGNED_IN)
    uow.sessions.add(open_session(SIGNED_IN_SESSION_ID, SIGNED_IN.id, SESSION_TOKEN, now))
    if with_token:
        uow.provider_tokens.save(SIGNED_IN_SESSION_ID, token_from_grant(EXPIRING_GRANT, now))
    app.dependency_overrides[get_unit_of_work_factory] = lambda: lambda: uow
    return {SESSION_COOKIE: SESSION_TOKEN}
