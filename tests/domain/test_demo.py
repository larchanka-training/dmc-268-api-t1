"""Признаки демо-режима (change `demo-mode`): демо-пользователь и демо-репозиторий."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.demo import (
    DEMO_PROVIDER_USER_ID,
    demo_installation_repository,
    is_demo_repository,
    is_demo_user,
)
from app.domain.entities import (
    InstallationRepository,
    Repository,
    RepositoryPermissions,
    User,
)
from app.domain.enums import Provider

NOW = datetime(2026, 11, 16, tzinfo=UTC)
USER = User(
    id=UUID(int=1),
    provider=Provider.GITHUB,
    provider_user_id=DEMO_PROVIDER_USER_ID,
    login="demo",
    name=None,
    avatar_url=None,
    created_at=NOW,
    updated_at=NOW,
)
REPOSITORY = Repository(
    id=UUID(int=2),
    provider=Provider.GITHUB,
    provider_id="demo-1",
    full_name="demo-org/payments-api",
    default_branch="main",
    auto_review_enabled=True,
    created_at=NOW,
    updated_at=NOW,
    connected_at=NOW,
)


def test_demo_user_is_the_seeded_account() -> None:
    assert is_demo_user(USER)


def test_a_real_github_account_is_not_demo() -> None:
    assert not is_demo_user(replace(USER, provider_user_id="583231"))


def test_demo_account_of_another_provider_is_not_demo() -> None:
    assert not is_demo_user(replace(USER, provider=Provider.GITLAB))


def test_demo_repository_has_the_prefix() -> None:
    assert is_demo_repository(REPOSITORY)


def test_a_github_repository_is_not_demo() -> None:
    assert not is_demo_repository(replace(REPOSITORY, provider_id="101"))


def test_demo_prefix_of_another_provider_is_not_demo() -> None:
    assert not is_demo_repository(replace(REPOSITORY, provider=Provider.GITLAB))


def test_demo_repository_is_listed_read_only() -> None:
    assert demo_installation_repository(REPOSITORY) == InstallationRepository(
        provider_id="demo-1",
        full_name="demo-org/payments-api",
        default_branch="main",
        permissions=RepositoryPermissions(admin=False, maintain=False, push=False, triage=False),
    )
