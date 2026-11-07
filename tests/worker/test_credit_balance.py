"""Команда зачисления `python -m app.worker.credit_balance` (`review-billing`, D8)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.domain.entities import User
from app.domain.enums import Provider
from app.worker import credit_balance

from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 11, 7, tzinfo=UTC)


@pytest.fixture
def uow(monkeypatch: pytest.MonkeyPatch) -> FakeUnitOfWork:
    work = FakeUnitOfWork()
    work.users.add(User(UUID(int=0xA), Provider.GITHUB, "1", "alice", None, None, NOW, NOW))
    monkeypatch.setattr(credit_balance, "load_settings", lambda: None)
    monkeypatch.setattr(credit_balance, "build_container", lambda settings: SimpleNamespace(unit_of_work=lambda: work))
    return work


def test_a_credit_prints_the_new_balance(uow: FakeUnitOfWork, capsys: pytest.CaptureFixture[str]) -> None:
    assert credit_balance.main(["alice", "-0.5", "--note", "Bob's счёт", "--by", "admin"]) == 0

    assert capsys.readouterr().out == "баланс alice: -0.5 €\n"
    [credit] = uow.billing.credits
    assert (credit.note, credit.credited_by) == ("Bob's счёт", "admin")


@pytest.mark.parametrize(("login", "amount"), [("bob", "5"), ("alice", "пять")])
def test_a_refusal_exits_1_and_writes_nothing(
    uow: FakeUnitOfWork, capsys: pytest.CaptureFixture[str], login: str, amount: str
) -> None:
    assert credit_balance.main([login, amount, "--note", "x"]) == 1

    assert "не зачислено" in capsys.readouterr().err
    assert uow.billing.credits == []
