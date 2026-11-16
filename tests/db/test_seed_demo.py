"""Демо-данные на живой базе (change `demo-mode`): запись проходит ограничения схемы и идемпотентна."""

import datetime as dt

import pytest
from sqlalchemy import text

from app.application.use_cases.seed_demo import (
    DEMO_USER_ID,
    SeedOutcome,
    demo_dataset,
    seed_demo,
)
from app.domain.entities import User
from app.domain.enums import Provider
from app.domain.ids import new_id
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.UTC)
TABLES = ("users", "balance_credits", "repositories", "merge_requests", "review_runs", "review_run_steps", "findings")


def _counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for table in TABLES}


def test_seed_twice_writes_the_same_rows(clean_db) -> None:
    alice = User(new_id(), Provider.GITHUB, "1", "alice", None, None, NOW, NOW)
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.users.add(alice)
        work.commit()

    first = seed_demo(SqlAlchemyUnitOfWork(clean_db), demo_dataset(NOW))
    after_first = _counts(clean_db)
    second = seed_demo(SqlAlchemyUnitOfWork(clean_db), demo_dataset(NOW + dt.timedelta(days=1)))

    assert (first, second) == (SeedOutcome(repositories=2, runs=3), SeedOutcome(repositories=0, runs=0))
    assert _counts(clean_db) == after_first
    assert after_first == {
        "users": 2,
        "balance_credits": 1,
        "repositories": 2,
        "merge_requests": 3,
        "review_runs": 3,
        "review_run_steps": 11,
        "findings": 4,
    }
    with SqlAlchemyUnitOfWork(clean_db) as work:
        assert work.users.get(alice.id) == alice
        assert work.billing.balance(DEMO_USER_ID) == 10
