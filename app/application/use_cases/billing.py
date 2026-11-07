"""Ручное зачисление на баланс аккаунта (`review-billing`, design D8).

Пока пополнения через платёжную систему нет, деньги зачисляет администратор командой
`python -m app.worker.credit_balance`; решение — найден ли логин и точное ли число сумма — здесь.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.domain.entities import BalanceCredit
from app.domain.enums import Provider

# Сумма хранится как `NUMERIC(20, 10)`: 10 знаков после точки и 10 до неё. Больше — база
# округлила бы молча или отказала.
_SCALE = Decimal("1e-10")
_LIMIT = Decimal("1e10")


class CreditRefusal(StrEnum):
    """Почему зачисление не записано."""

    UNKNOWN_LOGIN = "unknown_login"
    NOT_A_NUMBER = "not_a_number"


def credit_balance(
    uow: UnitOfWork,
    login: str,
    amount: str,
    *,
    note: str,
    credited_by: str,
    now: datetime,
    new_id: Callable[[], UUID],
) -> Decimal | CreditRefusal:
    """Зачислить `amount` евро аккаунту с логином GitHub `login` и вернуть его новый баланс.

    Отрицательная сумма — возврат или корректировка. Сумма не точное число в пределах колонки —
    `NOT_A_NUMBER`, логина нет среди пользователей сервиса — `UNKNOWN_LOGIN`; при отказе журнал
    не меняется.
    """
    parsed = _exact_amount(amount)
    if parsed is None:
        return CreditRefusal.NOT_A_NUMBER
    with uow:
        user = uow.users.find_by_login(Provider.GITHUB, login)
        if user is None:
            return CreditRefusal.UNKNOWN_LOGIN
        uow.billing.add_credit(BalanceCredit(new_id(), user.id, parsed, note, credited_by, now))
        uow.commit()
    with uow:
        return uow.billing.balance(user.id)


def _exact_amount(text: str) -> Decimal | None:
    try:
        amount = Decimal(text)
        exact = amount.is_finite() and abs(amount) < _LIMIT and amount.quantize(_SCALE) == amount
    except InvalidOperation:
        return None
    return amount if exact else None
