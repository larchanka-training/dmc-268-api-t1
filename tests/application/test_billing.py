"""Ручное зачисление на баланс (`review-billing`, design D8)."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.use_cases.billing import Credited, CreditRefusal, credit_balance
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
