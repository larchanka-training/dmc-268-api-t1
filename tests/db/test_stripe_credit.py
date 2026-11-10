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


def _paid_session(user: User) -> CheckoutSession:
    return CheckoutSession(SESSION, "payment", "paid", "eur", 500, str(user.id))


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
                SqlAlchemyUnitOfWork(clean_db), _paid_session(alice), event_id="evt_2", now=NOW, new_id=new_id
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
