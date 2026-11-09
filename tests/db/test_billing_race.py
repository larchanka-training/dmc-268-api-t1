"""Два прогона одного плательщика на последние деньги — на живой базе (`review-billing`)."""

import datetime as dt
import threading
import time
from dataclasses import replace
from decimal import Decimal
from typing import Self
from uuid import UUID

import pytest

from app.application.review_pipeline import reserve_or_refuse
from app.domain.entities import BalanceCredit, MergeRequest, Repository, ReviewRun, User
from app.domain.enums import (
    CoverageReason,
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.ids import new_id
from app.domain.llm_usage import LlmUsage
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.UTC)


def _run(merge_request_id: UUID, status: ReviewRunStatus = ReviewRunStatus.BUILDING_CONTEXT) -> ReviewRun:
    return ReviewRun(
        id=new_id(),
        merge_request_id=merge_request_id,
        head_sha="abc123",
        status=status,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _seed(clean_db) -> tuple[UUID, ReviewRun, ReviewRun]:
    """Плательщик с 0.06 €, цена модели 0.00001 € за токен и два прогона на разных PR."""
    payer = User(new_id(), Provider.GITHUB, "1", "alice", None, None, NOW, NOW)
    repository = Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id="1",
        full_name="acme/api",
        default_branch="main",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
        connected_at=NOW,
        payer_user_id=payer.id,
    )
    runs = []
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.users.add(payer)
        work.repositories.add(repository)
        for number in (1, 2):
            merge_request = MergeRequest(
                new_id(), repository.id, number, "t", "", "a", "feat", "main", "abc123",
                MergeRequestState.OPEN, NOW, NOW,
            )
            work.merge_requests.add(merge_request)
            run = _run(merge_request.id)
            work.review_runs.add(run)
            runs.append(run)
        # История цены — прогон без плательщика: баланс он не трогает.
        work.review_runs.add(
            replace(
                _run(merge_request.id, ReviewRunStatus.COMPLETED),
                head_sha="old",
                model="m",
                usage=LlmUsage(input_tokens=1000, total_tokens=1000, cost_eur=Decimal("0.01")),
            )
        )
        work.billing.add_credit(BalanceCredit(new_id(), payer.id, Decimal("0.06"), "тест", "admin", NOW))
        work.commit()
    return payer.id, runs[0], runs[1]


def _check(uow: SqlAlchemyUnitOfWork, run: ReviewRun) -> ReviewRun:
    # Оценка: 0.00001 × 2 × (2000 + 500) = 0.05 €.
    return reserve_or_refuse(
        uow, run, tokens=2000, max_output_tokens=500, now=NOW, duration_seconds=1.0
    )


def test_two_runs_on_the_last_money_reserve_once(clean_db) -> None:
    payer, first, second = _seed(clean_db)
    locked = threading.Event()

    class HoldsTheLock(SqlAlchemyUnitOfWork):
        """Первый держит блокировку плательщика, пока второй уже пришёл за своей."""

        def __enter__(self) -> Self:
            work = super().__enter__()
            lock = work.billing.lock_payer

            def lock_and_hold(user_id: UUID) -> None:
                lock(user_id)
                locked.set()
                time.sleep(0.3)

            work.billing.lock_payer = lock_and_hold  # type: ignore[method-assign]
            return work

    results: dict[str, ReviewRun] = {}

    def late() -> None:
        locked.wait(timeout=10)
        results["second"] = _check(SqlAlchemyUnitOfWork(clean_db), second)

    thread = threading.Thread(target=late)
    thread.start()
    results["first"] = _check(HoldsTheLock(clean_db), first)
    thread.join(timeout=10)

    assert results["first"].estimated_cost_eur == Decimal("0.05")
    assert results["first"].status is ReviewRunStatus.BUILDING_CONTEXT
    assert (results["second"].status, results["second"].coverage_reason) == (
        ReviewRunStatus.COMPLETED,
        CoverageReason.BALANCE,
    )
    with SqlAlchemyUnitOfWork(clean_db) as work:
        assert work.billing.balance(payer) == Decimal("0.01")
        stored = work.review_runs.get(second.id)
    assert stored is not None and (stored.estimated_cost_eur, stored.payer_user_id) == (None, payer)
