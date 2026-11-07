"""Ручное зачисление на баланс (`review-billing`, design D8)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.use_cases.billing import CreditRefusal, credit_balance
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


def _credit(uow: FakeUnitOfWork, login: str = "alice", amount: str = "5") -> Decimal | CreditRefusal:
    return credit_balance(uow, login, amount, note="тест", credited_by="admin", now=NOW, new_id=lambda: CREDIT_ID)


def test_a_credit_is_journaled_and_the_new_balance_returned() -> None:
    uow = _uow()

    assert _credit(uow) == Decimal(5)
    assert _credit(uow, amount="-0.5000000001") == Decimal("4.4999999999")
    assert uow.billing.credits[0] == BalanceCredit(CREDIT_ID, ALICE.id, Decimal(5), "тест", "admin", NOW)


def test_the_login_is_matched_ignoring_case() -> None:
    assert _credit(_uow(), login="Alice") == Decimal(5)


def test_an_unknown_login_writes_nothing() -> None:
    uow = _uow()

    assert _credit(uow, login="bob") is CreditRefusal.UNKNOWN_LOGIN
    assert uow.billing.credits == []


@pytest.mark.parametrize("amount", ["пять", "", "NaN", "Infinity", "1e400", "0.00000000001", "1,5"])
def test_an_amount_that_is_not_an_exact_number_writes_nothing(amount: str) -> None:
    uow = _uow()

    assert _credit(uow, amount=amount) is CreditRefusal.NOT_A_NUMBER
    assert uow.billing.credits == []
