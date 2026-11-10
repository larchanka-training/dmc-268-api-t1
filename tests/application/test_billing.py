"""Ручное зачисление на баланс (`review-billing`, design D8)."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.use_cases.billing import (
    CheckoutSession,
    Credited,
    CreditRefusal,
    StripeOutcome,
    credit_balance,
    credit_stripe_checkout,
)
from app.domain.entities import BalanceCredit, User
from app.domain.enums import Provider

from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
ALICE = User(UUID(int=0xA), Provider.GITHUB, "1", "alice", None, None, NOW, NOW)
CREDIT_ID = UUID(int=0xC)


def _uow() -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    uow.users.add(ALICE)
    return uow


def _credit(uow: FakeUnitOfWork, github_id: str = "1", amount: str = "5") -> Credited | CreditRefusal:
    return credit_balance(uow, github_id, amount, note="тест", credited_by="admin", now=NOW, new_id=lambda: CREDIT_ID)


def test_a_credit_is_journaled_and_the_new_balance_returned() -> None:
    uow = _uow()

    assert _credit(uow) == Credited("alice", Decimal(5))
    assert _credit(uow, amount="-0.5000000001") == Credited("alice", Decimal("4.4999999999"))
    assert uow.billing.credits[0] == BalanceCredit(CREDIT_ID, ALICE.id, Decimal(5), "тест", "admin", NOW)


def test_a_login_that_changed_hands_does_not_route_the_money() -> None:
    """Ревью #396: у нас записан `bob` (id 1), сменивший логин; `bob` теперь у id 2, он у нас не входил."""
    uow = FakeUnitOfWork()
    uow.users.add(replace(ALICE, login="bob"))

    assert _credit(uow, github_id="2") is CreditRefusal.UNKNOWN_ACCOUNT
    assert _credit(uow, github_id="bob") is CreditRefusal.UNKNOWN_ACCOUNT
    assert uow.billing.credits == []


@pytest.mark.parametrize("amount", ["пять", "", "NaN", "Infinity", "1e400", "0.00000000001", "1,5"])
def test_an_amount_that_is_not_an_exact_number_writes_nothing(amount: str) -> None:
    uow = _uow()

    assert _credit(uow, amount=amount) is CreditRefusal.NOT_A_NUMBER
    assert uow.billing.credits == []


# Пополнение через Stripe (change stripe-topup): сессия Checkout, запрошенная по thin-событию.

SESSION = "cs_test_a1"
PAID = CheckoutSession(SESSION, "payment", "paid", "eur", 1000, str(ALICE.id))


def _stripe(uow: FakeUnitOfWork, session: CheckoutSession, limit: Decimal = Decimal(20)) -> StripeOutcome:
    return credit_stripe_checkout(uow, session, limit=limit, event_id="evt_1", now=NOW, new_id=lambda: CREDIT_ID)


def test_a_paid_checkout_session_is_credited_in_euros() -> None:
    uow = _uow()

    assert _stripe(uow, replace(PAID, amount_total=1050)) is StripeOutcome.CREDITED
    assert uow.billing.credits == [
        BalanceCredit(CREDIT_ID, ALICE.id, Decimal("10.5"), f"Stripe {SESSION}", "stripe", NOW, SESSION)
    ]


def test_a_session_credited_once_is_a_duplicate_afterwards() -> None:
    uow = _uow()
    _stripe(uow, PAID)

    assert _stripe(uow, PAID) is StripeOutcome.DUPLICATE
    assert len(uow.billing.credits) == 1


@pytest.mark.parametrize(
    ("session", "outcome"),
    [
        (replace(PAID, mode="setup", payment_status="no_payment_required", amount_total=0), StripeOutcome.NOT_PAID),
        (replace(PAID, mode="subscription"), StripeOutcome.NOT_ONE_OFF),
        (replace(PAID, payment_status="unpaid"), StripeOutcome.NOT_PAID),
        (replace(PAID, payment_status="no_payment_required"), StripeOutcome.NOT_PAID),
        (replace(PAID, currency="usd"), StripeOutcome.OTHER_CURRENCY),
        (replace(PAID, currency="EUR"), StripeOutcome.OTHER_CURRENCY),
        (replace(PAID, client_reference_id=None), StripeOutcome.UNKNOWN_ACCOUNT),
        (replace(PAID, client_reference_id="alice"), StripeOutcome.UNKNOWN_ACCOUNT),
        (replace(PAID, client_reference_id=str(UUID(int=0xB))), StripeOutcome.UNKNOWN_ACCOUNT),
        (replace(PAID, amount_total=0), StripeOutcome.MALFORMED),
        (replace(PAID, amount_total=None), StripeOutcome.MALFORMED),
    ],
)
def test_a_session_that_is_not_a_paid_eur_payment_of_a_known_account_credits_nothing(
    session: CheckoutSession, outcome: StripeOutcome
) -> None:
    uow = _uow()

    assert _stripe(uow, session) is outcome
    assert uow.billing.credits == []


@pytest.mark.parametrize(
    ("session", "level"),
    [
        (replace(PAID, client_reference_id=None), logging.WARNING),
        (replace(PAID, currency="usd"), logging.WARNING),
        (replace(PAID, amount_total=0), logging.WARNING),
        (replace(PAID, mode="subscription"), logging.WARNING),
        (replace(PAID, payment_status="unpaid"), logging.INFO),
        (PAID, logging.INFO),
    ],
)
def test_a_paid_session_left_without_credit_is_a_warning_with_the_ids(
    session: CheckoutSession, level: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.application.use_cases.billing")

    _stripe(_uow(), session)

    [record] = [r for r in caplog.records if r.getMessage() == "оплата Stripe обработана"]
    assert record.levelno == level
    assert (record.stripe_event_id, record.stripe_checkout_session_id) == ("evt_1", SESSION)  # type: ignore[attr-defined]


def test_stripe_credits_up_to_the_limit_inclusive_under_the_payer_lock() -> None:
    uow = _uow()

    assert _stripe(uow, PAID) is StripeOutcome.CREDITED
    assert _stripe(uow, replace(PAID, id="cs_test_a2")) is StripeOutcome.CREDITED
    assert uow.billing.stripe_total(ALICE.id) == Decimal(20)
    assert uow.billing.locked == [ALICE.id, ALICE.id]


def test_a_payment_over_the_limit_is_not_credited_and_is_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    uow = _uow()
    _stripe(uow, PAID)
    caplog.set_level(logging.INFO, logger="app.application.use_cases.billing")
    caplog.clear()

    assert _stripe(uow, replace(PAID, id="cs_test_a2", amount_total=1001)) is StripeOutcome.OVER_LIMIT
    assert uow.billing.stripe_total(ALICE.id) == Decimal(10)
    [record] = [r for r in caplog.records if r.getMessage() == "оплата Stripe обработана"]
    assert (record.levelno, record.stripe_outcome) == (logging.WARNING, "over_limit")  # type: ignore[attr-defined]
    assert record.stripe_checkout_session_id == "cs_test_a2"  # type: ignore[attr-defined]


def test_a_repeated_session_after_the_limit_is_reached_is_a_duplicate() -> None:
    uow = _uow()
    _stripe(uow, PAID)
    _stripe(uow, replace(PAID, id="cs_test_a2"))

    assert _stripe(uow, PAID) is StripeOutcome.DUPLICATE
    assert len(uow.billing.credits) == 2


def test_manual_credits_do_not_count_toward_the_stripe_limit() -> None:
    uow = _uow()
    _credit(uow, amount="100")

    assert _stripe(uow, PAID) is StripeOutcome.CREDITED
