"""Вошедший пользователь для тестов ручек, которым сессия не предмет проверки."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI

from app.api.session import current_user
from app.domain.entities import User
from app.domain.enums import Provider

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


def sign_in(app: FastAPI, user: User = SIGNED_IN) -> None:
    """Подменить проверку сессии: запросы идут от `user`."""
    app.dependency_overrides[current_user] = lambda: user
