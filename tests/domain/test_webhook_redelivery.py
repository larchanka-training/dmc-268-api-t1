"""Чистое решение, какие доставки вебхука переотправить (#149), без сети.

Поля доставки — как в ответе `GET /app/hook/deliveries` (документация GitHub REST,
«List deliveries for an app webhook»): `guid` один у доставки и всех её
переотправок, у переотправки `redelivery = true`, успех — `status == "OK"`.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.webhook_redelivery import (
    REDELIVERY_WINDOW,
    RedeliveryPolicy,
    WebhookDelivery,
    select_redeliveries,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
POLICY = RedeliveryPolicy(max_attempts=3, backoff=timedelta(minutes=5), limit=10)


def delivery(
    id: int,
    guid: str = "g-1",
    *,
    ago: timedelta = timedelta(hours=1),
    status_code: int = 500,
    status: str | None = None,
    redelivery: bool = False,
    event: str = "pull_request",
    action: str | None = "synchronize",
) -> WebhookDelivery:
    return WebhookDelivery(
        id=id,
        guid=guid,
        delivered_at=NOW - ago,
        redelivery=redelivery,
        status=status if status is not None else ("OK" if status_code < 300 else "Invalid HTTP Response: 500"),
        status_code=status_code,
        event=event,
        action=action,
    )


def ids(chosen: list[WebhookDelivery]) -> list[int]:
    return [d.id for d in chosen]


def test_a_failed_delivery_without_success_is_redelivered() -> None:
    assert ids(select_redeliveries([delivery(1)], NOW, POLICY)) == [1]


def test_a_successful_delivery_is_left_alone() -> None:
    assert select_redeliveries([delivery(1, status_code=202)], NOW, POLICY) == []


def test_a_guid_already_redelivered_successfully_is_left_alone() -> None:
    deliveries = [
        delivery(2, ago=timedelta(minutes=30), status_code=202, redelivery=True),
        delivery(1),
    ]
    assert select_redeliveries(deliveries, NOW, POLICY) == []


def test_a_delivery_without_a_response_counts_as_failure() -> None:
    """Получатель не ответил: GitHub пишет 502 и `status` — не `OK`."""
    # Так GitHub пишет доставку без ответа получателя (проверено 2026-10-24).
    timed_out = delivery(1, status_code=502, status="failed to connect to host")
    assert ids(select_redeliveries([timed_out], NOW, POLICY)) == [1]


def test_a_redirect_is_a_failure_for_us() -> None:
    """3xx GitHub не проходит дальше: событие до приёма не дошло."""
    assert ids(select_redeliveries([delivery(1, status_code=301, status="Found")], NOW, POLICY)) == [1]


@pytest.mark.parametrize(
    ("event", "action"),
    [
        ("pull_request", "opened"),
        ("pull_request", "synchronize"),
        ("pull_request_review_thread", "resolved"),
        ("pull_request_review_thread", "unresolved"),
    ],
)
def test_events_of_the_intake_are_redelivered(event: str, action: str) -> None:
    assert ids(select_redeliveries([delivery(1, event=event, action=action)], NOW, POLICY)) == [1]


@pytest.mark.parametrize(
    ("event", "action"),
    [
        ("pull_request", "closed"),
        ("pull_request", "reopened"),
        ("pull_request_target", "opened"),
        ("push", None),
        ("installation", "created"),
    ],
)
def test_events_the_intake_ignores_are_not_redelivered(event: str, action: str | None) -> None:
    assert select_redeliveries([delivery(1, event=event, action=action)], NOW, POLICY) == []


def test_attempts_are_capped_per_guid() -> None:
    deliveries = [
        delivery(4, ago=timedelta(hours=1), redelivery=True),
        delivery(3, ago=timedelta(hours=2), redelivery=True),
        delivery(2, ago=timedelta(hours=3), redelivery=True),
        delivery(1, ago=timedelta(hours=4)),
    ]
    assert select_redeliveries(deliveries, NOW, POLICY) == []


def test_backoff_doubles_with_every_attempt() -> None:
    """Две переотправки — пауза 5 мин × 2² = 20 минут от последней попытки."""
    def history(last_ago: timedelta) -> list[WebhookDelivery]:
        return [
            delivery(3, ago=last_ago, redelivery=True),
            delivery(2, ago=timedelta(hours=1), redelivery=True),
            delivery(1, ago=timedelta(hours=2)),
        ]

    assert select_redeliveries(history(timedelta(minutes=19)), NOW, POLICY) == []
    assert ids(select_redeliveries(history(timedelta(minutes=20)), NOW, POLICY)) == [3]


def test_the_first_redelivery_waits_the_base_backoff() -> None:
    assert select_redeliveries([delivery(1, ago=timedelta(minutes=4))], NOW, POLICY) == []
    assert ids(select_redeliveries([delivery(1, ago=timedelta(minutes=5))], NOW, POLICY)) == [1]


def test_the_latest_attempt_of_a_guid_is_redelivered() -> None:
    deliveries = [delivery(2, ago=timedelta(hours=1), redelivery=True), delivery(1, ago=timedelta(hours=2))]
    assert ids(select_redeliveries(deliveries, NOW, POLICY)) == [2]


def test_deliveries_older_than_the_window_are_skipped() -> None:
    """GitHub переотправляет только доставки за трое суток; окно — с запасом."""
    assert REDELIVERY_WINDOW < timedelta(days=3)
    old = delivery(1, ago=REDELIVERY_WINDOW + timedelta(seconds=1))
    edge = delivery(2, "g-2", ago=REDELIVERY_WINDOW - timedelta(seconds=1))
    assert ids(select_redeliveries([edge, old], NOW, POLICY)) == [2]


def test_all_due_guids_are_returned_oldest_first_regardless_of_the_limit() -> None:
    """После простоя доставки одного PR приходят в порядке push'ей. Лимит считает
    сценарий по настоящим переотправкам: устаревший кандидат не должен занимать место."""
    deliveries = [delivery(n, f"g-{n}", ago=timedelta(minutes=100 - n)) for n in reversed(range(1, 6))]
    policy = RedeliveryPolicy(max_attempts=3, backoff=timedelta(minutes=5), limit=3)
    assert ids(select_redeliveries(deliveries, NOW, policy)) == [1, 2, 3, 4, 5]


def test_order_is_by_the_first_attempt_of_a_guid() -> None:
    deliveries = [
        delivery(3, "g-a", ago=timedelta(minutes=30), redelivery=True),
        delivery(2, "g-b", ago=timedelta(minutes=60)),
        delivery(1, "g-a", ago=timedelta(minutes=90)),
    ]
    assert ids(select_redeliveries(deliveries, NOW, POLICY)) == [3, 2]
