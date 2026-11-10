"""Приём thin-событий Stripe: зачисление оплаты Checkout на баланс (change `stripe-topup`).

Путь `/api/v1/webhooks/stripe` лежит под префиксом вебхуков: `Origin` не проверяется, тело
читается с лимитом вебхука (`origin.py`, `body_limit.py`). Роутер подключается раньше
`/webhooks/{provider}` (`factory.py`), иначе `stripe` ушёл бы в параметр провайдера VCS.

Порядок: без клиента Stripe (`STRIPE_WEBHOOK_SECRET` или `STRIPE_API_KEY` пуст) — `503
PAYMENTS_DISABLED`, тело не читается; подпись `Stripe-Signature` проверяет SDK по сырому телу с
допуском 300 с — неверная, устаревшая или тело не thin-событие — `400 WEBHOOK_SIGNATURE_INVALID`;
событие не об оплате Checkout — `200` без запросов; иначе сессия запрашивается у Stripe (сбой —
`502 PAYMENT_PROVIDER_UNAVAILABLE`) и обычным словарём уходит в `credit_stripe_checkout`. Сбой
базы — `500`. На 5xx Stripe повторяет доставку.
"""

import logging
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Literal

import stripe
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from stripe.events import (
    V1CheckoutSessionAsyncPaymentSucceededEventNotification,
    V1CheckoutSessionCompletedEventNotification,
)

from app.api.dependencies import get_container
from app.api.errors import (
    ERRORS_400_STRIPE_SIGNATURE,
    ERRORS_413_BODY,
    ERRORS_502_PAYMENT_PROVIDER,
    ERRORS_503_PAYMENTS_DISABLED,
    ApiError,
)
from app.api.problems import ErrorCode
from app.application.use_cases.billing import StripeOutcome, credit_stripe_checkout
from app.domain.ids import new_id

router = APIRouter(prefix="/api/v1")
_log = logging.getLogger(__name__)

# Оплата картой — `completed` с `paid`; отложенная — `completed` с `unpaid`, затем
# `async_payment_succeeded` той же сессии (design D6).
_PAYMENT_EVENTS = (V1CheckoutSessionCompletedEventNotification, V1CheckoutSessionAsyncPaymentSucceededEventNotification)

ReceivedStatus = Literal["credited", "duplicate", "ignored"]


class StripeWebhookReceived(BaseModel):
    """Тело `200` приёма события Stripe."""

    status: ReceivedStatus = Field(
        description=(
            "`credited` — оплата зачислена; `duplicate` — эта сессия оплаты уже зачислена; `ignored` — "
            "событие ничего не зачисляет: другое событие, не разовая оплата, неоплаченная сессия, не "
            "евро, неизвестный пользователь в `client_reference_id`."
        )
    )


_STATUS: dict[StripeOutcome, ReceivedStatus] = {StripeOutcome.CREDITED: "credited", StripeOutcome.DUPLICATE: "duplicate"}


@router.post(
    "/webhooks/stripe",
    response_model=StripeWebhookReceived,
    tags=["webhooks"],
    # Без сессии: доставку подтверждает подпись.
    openapi_extra={"security": []},
    summary="Принять thin-событие Stripe и зачислить оплату на баланс",
    description=(
        "Без сессии и без проверки Origin: доставку подтверждает подпись `Stripe-Signature`. По "
        "`v1.checkout.session.completed` и `v1.checkout.session.async_payment_succeeded` сервис запрашивает "
        "сессию Checkout у Stripe и, если у неё `mode = payment`, `payment_status = paid` и валюта `eur`, "
        "зачисляет `amount_total / 100` € пользователю из `client_reference_id` — не больше одного раза на "
        "сессию. Остальное — `200` без зачисления."
    ),
    response_description="Событие принято; в `status` — зачислено ли.",
    responses={
        **ERRORS_400_STRIPE_SIGNATURE,
        **ERRORS_413_BODY,
        **ERRORS_502_PAYMENT_PROVIDER,
        **ERRORS_503_PAYMENTS_DISABLED,
    },
)
async def receive_stripe_webhook(request: Request) -> StripeWebhookReceived:
    container = get_container(request)
    client = container.stripe_client()
    if client is None:
        raise ApiError(
            HTTPStatus.SERVICE_UNAVAILABLE,
            ErrorCode.PAYMENTS_DISABLED,
            "Приём оплаты Stripe не настроен: событие не принято, ничего не зачислено.",
        )
    body = await request.body()
    secret = container.settings.stripe_webhook_secret.get_secret_value()
    try:
        notification = client.parse_event_notification(body, request.headers.get("Stripe-Signature"), secret)
    # ValueError — не JSON или snapshot-событие, KeyError — нет полей thin-события.
    except (stripe.SignatureVerificationError, ValueError, KeyError):
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            ErrorCode.WEBHOOK_SIGNATURE_INVALID,
            "Подписи Stripe-Signature нет, она не совпала с телом или устарела, или тело не thin-событие Stripe.",
        ) from None
    if not isinstance(notification, _PAYMENT_EVENTS):
        return StripeWebhookReceived(status="ignored")
    try:
        # SDK синхронный: запрос — в threadpool. `to_dict`: с 15-й версии `StripeObject` не словарь.
        session = (await run_in_threadpool(notification.fetch_related_object)).to_dict()
    except stripe.StripeError as error:
        # Только тип и HTTP-статус: в тексте ошибки Stripe бывает маска ключа.
        _log.warning(
            "Stripe не отдал сессию оплаты",
            extra={"stripe_event_id": notification.id, "error_type": type(error).__name__, "http_status": error.http_status},
        )
        raise ApiError(
            HTTPStatus.BAD_GATEWAY,
            ErrorCode.PAYMENT_PROVIDER_UNAVAILABLE,
            "Stripe не отдал сессию оплаты: ничего не зачислено, Stripe повторит доставку.",
        ) from None
    outcome = await run_in_threadpool(
        credit_stripe_checkout,
        container.unit_of_work(),
        session,
        event_id=notification.id,
        now=datetime.now(UTC),
        new_id=new_id,
    )
    return StripeWebhookReceived(status=_STATUS.get(outcome, "ignored"))
