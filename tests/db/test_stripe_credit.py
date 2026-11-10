"""Одно зачисление на сессию оплаты Stripe — на живой базе (change `stripe-topup`, D6)."""

import datetime as dt
import threading
from decimal import Decimal

import pytest

from app.application.use_cases.billing import (
    CheckoutSession,
    StripeOutcome,
    credit_stripe_checkout,
)
from app.domain.entities import BalanceCredit, User
from app.domain.enums import Provider
from app.domain.ids import new_id
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.UTC)
SESSION = "cs_test_a1"


def _alice(engine) -> User:
    alice = User(new_id(), Provider.GITHUB, "1", "alice", None, None, NOW, NOW)
    with SqlAlchemyUnitOfWork(engine) as work:
        work.users.add(alice)
        work.commit()
    return alice


def _stripe_credit(user: User, session_id: str | None = SESSION) -> BalanceCredit:
    return BalanceCredit(new_id(), user.id, Decimal(5), "Stripe", "stripe", NOW, session_id)


def _paid_session(user: User, session_id: str = SESSION, cents: int = 500) -> CheckoutSession:
    return CheckoutSession(session_id, "payment", "paid", "eur", cents, str(user.id))


def _balance(engine, user: User) -> Decimal:
    with SqlAlchemyUnitOfWork(engine) as work:
        return work.billing.balance(user.id)


def test_a_session_is_credited_once_and_manual_credits_are_not_limited(clean_db) -> None:
    alice = _alice(clean_db)
    with SqlAlchemyUnitOfWork(clean_db) as work:
        assert work.billing.add_credit(_stripe_credit(alice)) is True
        work.commit()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        assert work.billing.add_credit(_stripe_credit(alice)) is False
        # Ручные зачисления без сессии индекс не ограничивает.
        assert work.billing.add_credit(_stripe_credit(alice, None)) is True
        assert work.billing.add_credit(_stripe_credit(alice, None)) is True
        work.commit()

    assert _balance(clean_db, alice) == Decimal(15)


def test_two_deliveries_of_one_session_at_once_credit_once(clean_db) -> None:
    """Вторая доставка вставляет, пока первая не закоммитила: проверка до вставки гонку бы пропустила."""
    alice = _alice(clean_db)
    inserted = threading.Event()
    outcomes: list[StripeOutcome] = []

    def second_delivery() -> None:
        inserted.wait(5)
        outcomes.append(
            credit_stripe_checkout(
                SqlAlchemyUnitOfWork(clean_db),
                _paid_session(alice),
                limit=Decimal(20),
                event_id="evt_2",
                now=NOW,
                new_id=new_id,
            )
        )

    second = threading.Thread(target=second_delivery)
    second.start()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        assert work.billing.add_credit(_stripe_credit(alice)) is True
        inserted.set()
        # Вторая доставка ждёт коммита первой на уникальном индексе.
        second.join(0.5)
        assert second.is_alive()
        work.commit()
    second.join(5)

    assert outcomes == [StripeOutcome.DUPLICATE]
    assert _balance(clean_db, alice) == Decimal(5)


class _PausedBeforeCommit(SqlAlchemyUnitOfWork):
    """Первое зачисление держит транзакцию открытой, пока вторая сессия идёт в обход."""

    def __init__(self, engine, before_commit) -> None:
        super().__init__(engine)
        self._before_commit = before_commit

    def commit(self) -> None:
        self._before_commit()
        super().commit()


def test_two_sessions_over_the_limit_at_once_credit_one(clean_db) -> None:
    """Без блокировки плательщика каждая сессия видит сумму без другой и обе проходят потолок."""
    alice = _alice(clean_db)
    second_outcome: list[StripeOutcome] = []

    def second_session() -> None:
        second_outcome.append(
            credit_stripe_checkout(
                SqlAlchemyUnitOfWork(clean_db),
                _paid_session(alice, "cs_test_b", 1500),
                limit=Decimal(20),
                event_id="evt_b",
                now=NOW,
                new_id=new_id,
            )
        )

    second = threading.Thread(target=second_session)

    def run_second_meanwhile() -> None:
        second.start()
        # Вторая сессия ждёт первую на блокировке плательщика.
        second.join(0.5)
        assert second.is_alive()

    first = credit_stripe_checkout(
        _PausedBeforeCommit(clean_db, run_second_meanwhile),
        _paid_session(alice, "cs_test_a", 1500),
        limit=Decimal(20),
        event_id="evt_a",
        now=NOW,
        new_id=new_id,
    )
    second.join(5)

    assert (first, second_outcome) == (StripeOutcome.CREDITED, [StripeOutcome.OVER_LIMIT])
    assert _balance(clean_db, alice) == Decimal(15)


def test_the_stripe_total_counts_only_stripe_credits(clean_db) -> None:
    alice = _alice(clean_db)
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.billing.add_credit(_stripe_credit(alice))
        work.billing.add_credit(_stripe_credit(alice, None))
        work.commit()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        assert work.billing.stripe_total(alice.id) == Decimal(5)
