"""Зачисление на баланс аккаунта (`review-billing`): вручную и по оплате Stripe.

Вручную деньги зачисляет администратор командой `python -m app.worker.credit_balance` (design D8
change `account-balance`); решение — найден ли аккаунт и точное ли число сумма — здесь. Аккаунт
ищется по числовому id GitHub, а не по логину: освободившийся логин GitHub отдаёт другому
человеку, а наша запись пользователя обновляет логин только при входе (ревью #396).

Оплату Stripe зачисляет вебхук (`app/api/stripe_webhook.py`, change `stripe-topup`): роут
проверяет подпись thin-события, запрашивает у Stripe сессию Checkout и отдаёт сюда её поля
(`CheckoutSession`); правило зачисления — здесь.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
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

_log = logging.getLogger(__name__)

class CreditRefusal(StrEnum):
    """Почему зачисление не записано."""

    UNKNOWN_ACCOUNT = "unknown_account"
    NOT_A_NUMBER = "not_a_number"


@dataclass(frozen=True)
class Credited:
    """Зачисление записано: логин получателя — чтобы администратор сверил, кому, — и новый баланс."""

    login: str
    balance: Decimal


def credit_balance(
    uow: UnitOfWork,
    github_id: str,
    amount: str,
    *,
    note: str,
    credited_by: str,
    now: datetime,
    new_id: Callable[[], UUID],
) -> Credited | CreditRefusal:
    """Зачислить `amount` евро аккаунту с id GitHub `github_id` и вернуть его логин и новый баланс.

    Отрицательная сумма — возврат или корректировка. Сумма не точное число в пределах колонки —
    `NOT_A_NUMBER`, аккаунта с таким id нет среди пользователей сервиса — `UNKNOWN_ACCOUNT`; при
    отказе журнал не меняется.
    """
    parsed = _exact_amount(amount)
    if parsed is None:
        return CreditRefusal.NOT_A_NUMBER
    with uow:
        user = uow.users.find_by_provider(Provider.GITHUB, github_id)
        if user is None:
            return CreditRefusal.UNKNOWN_ACCOUNT
        uow.billing.add_credit(BalanceCredit(new_id(), user.id, parsed, note, credited_by, now))
        uow.commit()
    with uow:
        return Credited(user.login, uow.billing.balance(user.id))


def _exact_amount(text: str) -> Decimal | None:
    try:
        amount = Decimal(text)
        exact = amount.is_finite() and abs(amount) < _LIMIT and amount.quantize(_SCALE) == amount
    except InvalidOperation:
        return None
    return amount if exact else None


@dataclass(frozen=True)
class CheckoutSession:
    """Поля сессии Checkout, по которым решается зачисление: только стабильные (design D3)."""

    id: str
    mode: str | None
    payment_status: str | None
    currency: str | None
    amount_total: int | None
    client_reference_id: str | None


class StripeOutcome(StrEnum):
    """Чем кончилось зачисление сессии Checkout."""

    CREDITED = "credited"
    DUPLICATE = "duplicate"
    # Деньги не получены.
    NOT_PAID = "not_paid"
    # Оплачено, но не разовой оплатой: продаём только пополнение, подписку — нет.
    NOT_ONE_OFF = "not_one_off"
    OTHER_CURRENCY = "other_currency"
    UNKNOWN_ACCOUNT = "unknown_account"
    # Суммы нет или она не положительна.
    MALFORMED = "malformed"


# Исходы после проверки `payment_status == "paid"`: деньги списаны, зачисления нет.
_PAID_NOT_CREDITED = frozenset(
    {StripeOutcome.NOT_ONE_OFF, StripeOutcome.OTHER_CURRENCY, StripeOutcome.UNKNOWN_ACCOUNT, StripeOutcome.MALFORMED}
)


def credit_stripe_checkout(
    uow: UnitOfWork, session: CheckoutSession, *, event_id: str, now: datetime, new_id: Callable[[], UUID]
) -> StripeOutcome:
    """Зачислить оплату сессии Checkout не больше одного раза на сессию.

    Повтор сессии — `DUPLICATE`: событие пришло снова или это второе событие той же сессии.
    Исход пишется строкой лога с id события и сессии — без суммы и пользователя.
    """
    outcome = _credit_once(uow, session, now, new_id)
    # Сессия уже оплачена, а на баланс не попала: зачислять руками (`make credit-balance`).
    level = logging.WARNING if outcome in _PAID_NOT_CREDITED else logging.INFO
    _log.log(
        level,
        "оплата Stripe обработана",
        extra={"stripe_event_id": event_id, "stripe_checkout_session_id": session.id, "stripe_outcome": outcome.value},
    )
    return outcome


def _credit_once(uow: UnitOfWork, session: CheckoutSession, now: datetime, new_id: Callable[[], UUID]) -> StripeOutcome:
    # Не `status`: у `complete` оплата может ещё идти, деньги получены только при `paid`.
    if session.payment_status != "paid":
        return StripeOutcome.NOT_PAID
    if session.mode != "payment":
        return StripeOutcome.NOT_ONE_OFF
    if session.currency != "eur":
        return StripeOutcome.OTHER_CURRENCY
    if session.amount_total is None or session.amount_total <= 0:
        return StripeOutcome.MALFORMED
    try:
        user_id = UUID(session.client_reference_id)
    except (TypeError, ValueError):
        return StripeOutcome.UNKNOWN_ACCOUNT
    with uow:
        if uow.users.get(user_id) is None:
            return StripeOutcome.UNKNOWN_ACCOUNT
        amount = Decimal(session.amount_total) / 100
        credit = BalanceCredit(new_id(), user_id, amount, f"Stripe {session.id}", "stripe", now, session.id)
        if not uow.billing.add_credit(credit):
            return StripeOutcome.DUPLICATE
        uow.commit()
    return StripeOutcome.CREDITED
