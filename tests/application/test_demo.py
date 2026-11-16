"""Демо-режим (change `demo-mode`): решение о демо-пользователе, вход демо, видимость и seed.

Без базы и сети: фейк unit of work. Запись в настоящую базу — `tests/db/test_seed_demo.py`.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.demo import demo_fallback
from app.application.use_cases.auth import demo_sign_in
from app.application.use_cases.connect_repository import (
    UserContext,
    list_user_repositories,
)
from app.application.use_cases.seed_demo import (
    DEMO_USER_ID,
    SeedOutcome,
    demo_dataset,
    seed_demo,
)
from app.domain.demo import DEMO_SESSION_ID, is_demo_repository
from app.domain.enums import ReviewRunStatus

from ..fakes.installation_catalog import FakeInstallationCatalog
from ..fakes.oauth import FakeOAuthProvider
from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("method", ["GET", "HEAD", "get"])
def test_read_without_session_in_demo_mode_falls_back(method: str) -> None:
    assert demo_fallback(True, method, has_session=False)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_change_without_session_never_falls_back(method: str) -> None:
    assert not demo_fallback(True, method, has_session=False)


def test_signed_in_user_stays_themselves() -> None:
    assert not demo_fallback(True, "GET", has_session=True)


def test_without_demo_mode_nothing_falls_back() -> None:
    assert not demo_fallback(False, "GET", has_session=False)


def _seeded() -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    seed_demo(uow, demo_dataset(NOW))
    return uow


def test_demo_sign_in_returns_the_seeded_user() -> None:
    signed_in = demo_sign_in(_seeded())

    assert signed_in is not None
    assert (signed_in.user.id, signed_in.session_id) == (DEMO_USER_ID, DEMO_SESSION_ID)


def test_demo_sign_in_without_seed_is_none() -> None:
    assert demo_sign_in(FakeUnitOfWork()) is None


def test_demo_user_sees_demo_repositories_without_the_provider() -> None:
    catalog = FakeInstallationCatalog()
    user = UserContext(oauth=FakeOAuthProvider(), session_id=DEMO_SESSION_ID, user_id=DEMO_USER_ID, demo=True)

    listed = list_user_repositories(catalog, _seeded(), user, NOW)

    assert [r.full_name for r in listed] == ["demo-org/payments-api", "demo-org/web-cabinet"]
    assert catalog.calls == []


def test_demo_user_does_not_see_a_real_repository_it_pays_for() -> None:
    uow = _seeded()
    demo = demo_dataset(NOW).repositories[0]
    uow.repositories.add(replace(demo, id=UUID(int=99), provider_id="101", full_name="octo-org/real"))
    user = UserContext(oauth=FakeOAuthProvider(), session_id=DEMO_SESSION_ID, user_id=DEMO_USER_ID, demo=True)

    listed = list_user_repositories(FakeInstallationCatalog(), uow, user, NOW)

    assert all(r.provider_id.startswith("demo-") for r in listed)


def test_dataset_covers_the_required_statuses() -> None:
    dataset = demo_dataset(NOW)

    assert {run.status for run in dataset.runs} == {
        ReviewRunStatus.COMPLETED,
        ReviewRunStatus.FAILED,
        ReviewRunStatus.ANALYSING,
    }
    assert all(is_demo_repository(r) and r.payer_user_id == DEMO_USER_ID for r in dataset.repositories)
    assert dataset.findings and dataset.steps


def test_dataset_is_deterministic_and_relative_to_now() -> None:
    assert demo_dataset(NOW) == demo_dataset(NOW)
    assert max(run.created_at for run in demo_dataset(NOW).runs) == NOW - timedelta(seconds=20)


def test_seed_writes_everything_once() -> None:
    uow = FakeUnitOfWork()
    dataset = demo_dataset(NOW)

    first = seed_demo(uow, dataset)
    second = seed_demo(uow, demo_dataset(NOW + timedelta(days=1)))

    assert first == SeedOutcome(repositories=2, runs=3)
    assert second == SeedOutcome(repositories=0, runs=0)
    assert uow.users.get(DEMO_USER_ID) == dataset.user
    assert [len(uow.findings.list_for_run(run.id)) for run in dataset.runs] == [4, 0, 0]
    assert [len(uow.review_run_steps.list_for_run(run.id)) for run in dataset.runs] == [5, 3, 3]
    assert uow.billing.balance(DEMO_USER_ID) == 10
