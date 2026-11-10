"""`POST /api/v1/webhooks/stripe` на TestClient с фейковым `UnitOfWork` (change `stripe-topup`).

Подпись считается здесь по схеме Stripe (`t=…,v1=HMAC-SHA256(secret, "{t}.{тело}")`), а не
функцией SDK, — так тест не сверяет SDK с ним же. Thin-событие проходит настоящий
`StripeClient.parse_event_notification`, сессию SDK запрашивает через подменённый HTTP-клиент —
сеть не трогается.
"""

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
import stripe
from fastapi.testclient import TestClient
from httpx2 import Response

from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import User
from app.domain.enums import Provider

from ..fakes.unit_of_work import FakeUnitOfWork

SECRET = "whsec_test_secret"
API_KEY = "rk_test_key"
PATH = "/api/v1/webhooks/stripe"
SIGNED_UP = datetime(2026, 11, 1, tzinfo=UTC)
ALICE = User(UUID(int=0xA), Provider.GITHUB, "1", "alice", None, None, SIGNED_UP, SIGNED_UP)
SESSION = "cs_test_a1"
COMPLETED = "v1.checkout.session.completed"
ASYNC_SUCCEEDED = "v1.checkout.session.async_payment_succeeded"


def checkout_session(**fields: Any) -> dict[str, Any]:
    session: dict[str, Any] = {
        "id": SESSION,
        "object": "checkout.session",
        "mode": "payment",
        "status": "complete",
        "payment_status": "paid",
        "currency": "eur",
        "amount_total": 1500,
        "client_reference_id": str(ALICE.id),
        "livemode": False,
    }
    session.update(fields)
    return session


@dataclass
class FakeStripeApi(stripe.HTTPClient):
    """API Stripe за SDK: отдаёт `session` на любой GET, запоминает пути."""

    session: dict[str, Any] = field(default_factory=checkout_session)
    status: int = 200
    error: Exception | None = None
    paths: list[str] = field(default_factory=list)
    name = "fake"

    def __post_init__(self) -> None:
        super().__init__()

    def request(self, method: str, url: str, headers: Any, post_data: Any = None, **_: Any) -> tuple[str, int, dict[str, str]]:
        self.paths.append(f"{method.upper()} {url.removeprefix(stripe.DEFAULT_API_BASE)}")
        assert headers["Authorization"] == f"Bearer {API_KEY}"
        if self.error is not None:
            raise self.error
        body = self.session if self.status == 200 else {"error": {"type": "api_error", "message": "сбой"}}
        return json.dumps(body), self.status, {}


@dataclass
class StubContainer:
    settings: Settings
    uow: FakeUnitOfWork
    client: stripe.StripeClient | None

    def unit_of_work(self) -> FakeUnitOfWork:
        return self.uow

    def stripe_client(self) -> stripe.StripeClient | None:
        return self.client


def make_client(api: FakeStripeApi | None = None, *, enabled: bool = True) -> tuple[TestClient, FakeUnitOfWork, FakeStripeApi]:
    api = api if api is not None else FakeStripeApi()
    settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="github-secret",
        stripe_webhook_secret=SECRET if enabled else "",
        stripe_api_key=API_KEY if enabled else "",
        public_origin="https://review.example",
    )
    uow = FakeUnitOfWork()
    uow.users.add(ALICE)
    client = stripe.StripeClient(API_KEY, http_client=api, max_network_retries=0) if enabled else None
    app = create_app(settings)
    app.state.container = StubContainer(settings, uow, client)
    return TestClient(app), uow, api


def thin_event(event_type: str = COMPLETED, session_id: str = SESSION, event_id: str = "evt_test_1") -> bytes:
    body = {
        "id": event_id,
        "object": "v2.core.event",
        "type": event_type,
        "livemode": False,
        "created": "2026-11-10T12:00:00.000Z",
        "context": None,
        "related_object": {"id": session_id, "type": "checkout.session", "url": f"/v1/checkout/sessions/{session_id}"},
    }
    return json.dumps(body).encode()


def sign(body: bytes, secret: str = SECRET, at: int | None = None) -> str:
    timestamp = int(time.time()) if at is None else at
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def post(client: TestClient, body: bytes, signature: str | None = "auto", **headers: str) -> Response:
    if signature == "auto":
        signature = sign(body)
    if signature is not None:
        headers["Stripe-Signature"] = signature
    return client.post(PATH, content=body, headers={"Content-Type": "application/json", **headers})


def test_a_paid_session_is_fetched_and_credited_once_however_many_events_arrive() -> None:
    client, uow, api = make_client()

    first = post(client, thin_event())
    again = post(client, thin_event())
    async_event = post(client, thin_event(ASYNC_SUCCEEDED, event_id="evt_test_2"))

    assert (first.status_code, first.json()) == (200, {"status": "credited"})
    assert again.json() == async_event.json() == {"status": "duplicate"}
    assert api.paths == [f"GET /v1/checkout/sessions/{SESSION}"] * 3
    [credit] = uow.billing.credits
    assert (credit.user_id, credit.amount_eur, credit.credited_by) == (ALICE.id, Decimal(15), "stripe")
    assert credit.stripe_checkout_session_id == SESSION


def test_an_async_payment_is_credited_when_it_succeeds() -> None:
    api = FakeStripeApi(checkout_session(payment_status="unpaid"))
    client, uow, _ = make_client(api)

    assert post(client, thin_event()).json() == {"status": "ignored"}
    assert uow.billing.credits == []
    api.session = checkout_session()
    assert post(client, thin_event(ASYNC_SUCCEEDED)).json() == {"status": "credited"}
    assert len(uow.billing.credits) == 1


# Каждое поле сессии, от которого зависит отказ: роут должен передать его в правило, а не потерять.
@pytest.mark.parametrize("field", [{"currency": "usd"}, {"mode": "setup"}, {"payment_status": "unpaid"}, {"amount_total": 0}])
def test_a_fetched_session_that_is_not_a_paid_eur_payment_credits_nothing(field: dict[str, object]) -> None:
    """Правило зачисления — в `tests/application/test_billing.py`; здесь — что роут отдаёт сессию ему."""
    client, uow, _ = make_client(FakeStripeApi(checkout_session(**field)))

    response = post(client, thin_event())

    assert (response.status_code, response.json()) == (200, {"status": "ignored"})
    assert uow.billing.credits == []


@pytest.mark.parametrize(
    "event_type", ["v1.checkout.session.async_payment_failed", "v1.checkout.session.expired", "v1.charge.refunded", "v2.core.account.updated"]
)
def test_another_event_is_accepted_without_asking_stripe(event_type: str) -> None:
    client, uow, api = make_client()

    response = post(client, thin_event(event_type))

    assert (response.status_code, response.json()) == (200, {"status": "ignored"})
    assert (api.paths, uow.billing.credits) == ([], [])


def test_the_key_and_secret_stay_out_of_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    client, _, _ = make_client(FakeStripeApi(checkout_session(currency="usd")))

    with caplog.at_level(logging.INFO):
        post(client, thin_event())

    assert caplog.records
    assert API_KEY not in caplog.text and SECRET not in caplog.text


@pytest.mark.parametrize(
    "api",
    [
        FakeStripeApi(status=500),
        FakeStripeApi(status=401),
        FakeStripeApi(status=404),
        FakeStripeApi(error=stripe.APIConnectionError("тайм-аут")),
    ],
)
def test_when_stripe_does_not_return_the_session_the_delivery_fails_for_a_retry(
    api: FakeStripeApi, caplog: pytest.LogCaptureFixture
) -> None:
    client, uow, _ = make_client(api)

    response = post(client, thin_event())

    assert response.status_code == 502
    assert response.json()["code"] == "PAYMENT_PROVIDER_UNAVAILABLE"
    assert uow.billing.credits == []
    assert API_KEY not in caplog.text


@pytest.mark.parametrize(
    "signature",
    [
        None,
        "",
        "garbage",
        sign(thin_event(), secret="whsec_other"),
        # Старше допуска SDK в 300 с.
        sign(thin_event(), at=int(time.time()) - 301),
        # Подпись другого тела.
        sign(thin_event(session_id="cs_other")),
    ],
)
def test_a_bad_missing_or_stale_signature_is_refused_without_asking_stripe(signature: str | None) -> None:
    client, uow, api = make_client()

    response = post(client, thin_event(), signature=signature)

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "WEBHOOK_SIGNATURE_INVALID"
    assert (api.paths, uow.billing.credits) == ([], [])


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        # Snapshot-событие: эндпоинт ждёт thin.
        json.dumps({"id": "evt_1", "object": "event", "type": "checkout.session.completed", "data": {"object": {}}}).encode(),
        # Thin-событие оплаты без `related_object`.
        json.dumps({"id": "evt_1", "object": "v2.core.event", "type": COMPLETED, "created": "2026-11-10T12:00:00Z"}).encode(),
    ],
)
def test_a_signed_body_that_is_not_a_thin_event_is_refused(body: bytes) -> None:
    client, uow, api = make_client()

    response = post(client, body)

    assert (response.status_code, response.json()["code"]) == (400, "WEBHOOK_SIGNATURE_INVALID")
    assert (api.paths, uow.billing.credits) == ([], [])


def test_without_the_secret_or_key_payments_are_disabled() -> None:
    client, uow, _ = make_client(enabled=False)

    response = post(client, thin_event(), signature=sign(thin_event(), secret=""))

    assert response.status_code == 503
    assert response.json()["code"] == "PAYMENTS_DISABLED"
    assert uow.billing.credits == []


def test_origin_is_not_checked_on_the_stripe_webhook() -> None:
    """Stripe не шлёт `Origin`; чужой `Origin` подпись тоже не отменяет."""
    client, _, _ = make_client()

    assert post(client, thin_event()).status_code == 200
    assert post(client, thin_event(), Origin="https://evil.example").status_code == 200
