"""Зачисление на баланс аккаунта (`review-billing`): вручную и по оплате Stripe.

Вручную деньги зачисляет администратор командой `python -m app.worker.credit_balance` (design D8
change `account-balance`); решение — найден ли аккаунт и точное ли число сумма — здесь. Аккаунт
ищется по числовому id GitHub, а не по логину: освободившийся логин GitHub отдаёт другому
человеку, а наша запись пользователя обновляет логин только при входе (ревью #396).

Оплату Stripe зачисляет вебхук (`app/api/stripe_webhook.py`, change `stripe-topup`): роут
проверяет подпись thin-события, запрашивает у Stripe сессию Checkout и отдаёт её сюда обычным
словарём; правило зачисления — здесь.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.domain.entities import BalanceCredit
from app.domain.enums import Provider

# Сумма хранится как `NUMERIC(20, 10)`: 10 знаков после точки и 10 до неё. Больше — база
# округлила бы молча или отказала.
_SCALE = Decimal("1e-10")
_LIMIT = Decimal("1e10")

_log = logging.getLogger(__name__)

STRIPE_CREDITED_BY = "stripe"


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


class StripeOutcome(StrEnum):
    """Чем кончилось зачисление сессии Checkout."""

    CREDITED = "credited"
    DUPLICATE = "duplicate"
    # Сессия не разовой оплаты: `setup` денег не берёт, `subscription` не продаём.
    NOT_A_PAYMENT = "not_a_payment"
    UNPAID = "unpaid"
    OTHER_CURRENCY = "other_currency"
    UNKNOWN_ACCOUNT = "unknown_account"
    # Сессия без id или сумма не целое положительное число центов в пределах колонки.
    MALFORMED = "malformed"


# Исходы после проверки `payment_status == "paid"`: деньги списаны, зачисления нет.
_PAID_NOT_CREDITED = frozenset(
    {StripeOutcome.OTHER_CURRENCY, StripeOutcome.UNKNOWN_ACCOUNT, StripeOutcome.MALFORMED}
)


@dataclass(frozen=True)
class StripeTopUp:
    """Оплаченная сессия Checkout, которую можно зачислить."""

    session_id: str
    user_id: UUID
    amount_eur: Decimal


def stripe_top_up(session: Mapping[str, Any]) -> StripeTopUp | StripeOutcome:
    """Что зачислить по сессии Checkout, или почему ничего.

    Читаются только стабильные поля сессии (`id`, `mode`, `payment_status`, `currency`,
    `amount_total`, `client_reference_id`). Есть ли такой пользователь, решает
    `credit_stripe_checkout`.
    """
    if session.get("mode") != "payment":
        return StripeOutcome.NOT_A_PAYMENT
    # Не `status`: у `complete` оплата может ещё идти, деньги получены только при `paid`.
    if session.get("payment_status") != "paid":
        return StripeOutcome.UNPAID
    if session.get("currency") != "eur":
        return StripeOutcome.OTHER_CURRENCY
    session_id, cents = session.get("id"), session.get("amount_total")
    # `type(...) is int`: `True` — тоже int.
    if not isinstance(session_id, str) or not session_id or type(cents) is not int or cents <= 0:
        return StripeOutcome.MALFORMED
    amount = Decimal(cents) / 100
    if amount >= _LIMIT:
        return StripeOutcome.MALFORMED
    try:
        user_id = UUID(session.get("client_reference_id"))
    except (TypeError, ValueError):
        return StripeOutcome.UNKNOWN_ACCOUNT
    return StripeTopUp(session_id, user_id, amount)


def credit_stripe_checkout(
    uow: UnitOfWork, session: Mapping[str, Any], *, event_id: str, now: datetime, new_id: Callable[[], UUID]
) -> StripeOutcome:
    """Зачислить оплату сессии Checkout не больше одного раза на сессию.

    Повтор сессии — `DUPLICATE`: событие пришло снова или это второе событие той же сессии.
    Исход пишется строкой лога с id события и сессии — без суммы и пользователя.
    """
    top_up = stripe_top_up(session)
    outcome = top_up if isinstance(top_up, StripeOutcome) else _credit_once(uow, top_up, now, new_id)
    # Сессия уже оплачена, а на баланс не попала: зачислять руками (`make credit-balance`).
    level = logging.WARNING if outcome in _PAID_NOT_CREDITED else logging.INFO
    _log.log(
        level,
        "оплата Stripe обработана",
        extra={
            "stripe_event_id": event_id,
            "stripe_checkout_session_id": session.get("id"),
            "stripe_outcome": outcome.value,
        },
    )
    return outcome


def _credit_once(uow: UnitOfWork, top_up: StripeTopUp, now: datetime, new_id: Callable[[], UUID]) -> StripeOutcome:
    with uow:
        if uow.users.get(top_up.user_id) is None:
            return StripeOutcome.UNKNOWN_ACCOUNT
        credit = BalanceCredit(
            new_id(),
            top_up.user_id,
            top_up.amount_eur,
            f"Stripe {top_up.session_id}",
            STRIPE_CREDITED_BY,
            now,
            top_up.session_id,
        )
        if not uow.billing.add_credit(credit):
            return StripeOutcome.DUPLICATE
        uow.commit()
    return StripeOutcome.CREDITED
