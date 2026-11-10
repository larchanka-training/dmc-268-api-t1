"""Ручное зачисление на баланс (`review-billing`, design D8)."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.use_cases.billing import (
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


def _session(**fields: object) -> dict[str, object]:
    session: dict[str, object] = {
        "id": SESSION,
        "object": "checkout.session",
        "mode": "payment",
        "status": "complete",
        "payment_status": "paid",
        "currency": "eur",
        "amount_total": 1000,
        "client_reference_id": str(ALICE.id),
    }
    session.update(fields)
    return session


def _stripe(uow: FakeUnitOfWork, session: dict[str, object]) -> StripeOutcome:
    return credit_stripe_checkout(uow, session, event_id="evt_1", now=NOW, new_id=lambda: CREDIT_ID)


def test_a_paid_checkout_session_is_credited_in_euros() -> None:
    uow = _uow()

    assert _stripe(uow, _session(amount_total=1050)) is StripeOutcome.CREDITED
    assert uow.billing.credits == [
        BalanceCredit(CREDIT_ID, ALICE.id, Decimal("10.5"), f"Stripe {SESSION}", "stripe", NOW, SESSION)
    ]


def test_a_session_credited_once_is_a_duplicate_afterwards() -> None:
    uow = _uow()
    _stripe(uow, _session())

    assert _stripe(uow, _session()) is StripeOutcome.DUPLICATE
    assert len(uow.billing.credits) == 1


@pytest.mark.parametrize(
    ("session", "outcome"),
    [
        (_session(mode="setup", payment_status="no_payment_required", amount_total=0), StripeOutcome.NOT_A_PAYMENT),
        (_session(mode="subscription"), StripeOutcome.NOT_A_PAYMENT),
        (_session(mode=None), StripeOutcome.NOT_A_PAYMENT),
        (_session(payment_status="unpaid"), StripeOutcome.UNPAID),
        (_session(payment_status="no_payment_required"), StripeOutcome.UNPAID),
        (_session(currency="usd"), StripeOutcome.OTHER_CURRENCY),
        (_session(currency="EUR"), StripeOutcome.OTHER_CURRENCY),
        (_session(currency=None), StripeOutcome.OTHER_CURRENCY),
        (_session(client_reference_id=None), StripeOutcome.UNKNOWN_ACCOUNT),
        (_session(client_reference_id="alice"), StripeOutcome.UNKNOWN_ACCOUNT),
        (_session(client_reference_id=str(UUID(int=0xB))), StripeOutcome.UNKNOWN_ACCOUNT),
        (_session(amount_total=0), StripeOutcome.MALFORMED),
        (_session(amount_total=-500), StripeOutcome.MALFORMED),
        (_session(amount_total=10.5), StripeOutcome.MALFORMED),
        (_session(amount_total="1000"), StripeOutcome.MALFORMED),
        (_session(amount_total=True), StripeOutcome.MALFORMED),
        (_session(amount_total=None), StripeOutcome.MALFORMED),
        (_session(amount_total=10**12), StripeOutcome.MALFORMED),
        (_session(id=None), StripeOutcome.MALFORMED),
    ],
)
def test_a_session_that_is_not_a_paid_eur_payment_of_a_known_account_credits_nothing(
    session: dict[str, object], outcome: StripeOutcome
) -> None:
    uow = _uow()

    assert _stripe(uow, session) is outcome
    assert uow.billing.credits == []


@pytest.mark.parametrize(
    ("session", "level"),
    [
        (_session(client_reference_id=None), logging.WARNING),
        (_session(currency="usd"), logging.WARNING),
        (_session(amount_total=0), logging.WARNING),
        (_session(payment_status="unpaid"), logging.INFO),
        (_session(), logging.INFO),
    ],
)
def test_a_paid_session_left_without_credit_is_a_warning(
    session: dict[str, object], level: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.application.use_cases.billing")

    _stripe(_uow(), session)

    assert [r.levelno for r in caplog.records if r.getMessage() == "оплата Stripe обработана"] == [level]


def test_the_largest_amount_the_column_holds_is_credited_exactly() -> None:
    uow = _uow()

    assert _stripe(uow, _session(amount_total=10**12 - 1)) is StripeOutcome.CREDITED
    assert uow.billing.credits[0].amount_eur == Decimal("9999999999.99")
