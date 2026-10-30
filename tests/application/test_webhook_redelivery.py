"""Сценарий повтора упавших доставок на фейках портов, без сети (#149).

Payload'ы — записанные тела GitHub из `tests/fixtures`: у `opened` голова
`a1b2…` совпадает с головой PR в `pr_metadata()`, у `synchronize` — другой
коммит, то есть доставка `synchronize` для этого PR уже устарела.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.application.webhook_redelivery import redeliver_failed_webhooks
from app.domain.vcs_errors import VcsError, VcsTransientError
from app.domain.webhook_redelivery import (
    REDELIVERY_WINDOW,
    RedeliveryPolicy,
    WebhookDelivery,
)

from ..fakes import INSTALLATION_ID, REPO_FULL_NAME, FakeVcs, webhook_payload

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
POLICY = RedeliveryPolicy(max_attempts=3, backoff=timedelta(minutes=5), limit=10)
BUDGET = 30.0


def delivery(
    id: int,
    guid: str,
    *,
    status_code: int = 500,
    ago: timedelta = timedelta(hours=1),
    event: str = "pull_request",
    action: str = "opened",
) -> WebhookDelivery:
    return WebhookDelivery(
        id=id,
        guid=guid,
        delivered_at=NOW - ago,
        redelivery=False,
        status="OK" if status_code < 300 else "Invalid HTTP Response: 500",
        status_code=status_code,
        event=event,
        action=action,
    )


class FakeDeliveries:
    def __init__(
        self,
        deliveries: list[WebhookDelivery],
        failures: dict[int, VcsError] | None = None,
        payload_failures: dict[int, VcsError] | None = None,
    ) -> None:
        self.deliveries = deliveries
        self.failures = failures or {}
        self.payload_failures = payload_failures or {}
        self.since: list[datetime] = []
        self.payloads_read: list[int] = []
        self.redelivered: list[int] = []

    def list_recent(self, since: datetime) -> list[WebhookDelivery]:
        self.since.append(since)
        return self.deliveries

    def delivery_payload(self, delivery_id: int) -> dict[str, Any] | None:
        self.payloads_read.append(delivery_id)
        if delivery_id in self.payload_failures:
            raise self.payload_failures[delivery_id]
        (found,) = [d for d in self.deliveries if d.id == delivery_id]
        assert found.action is not None
        return webhook_payload(found.action)

    def redeliver(self, delivery_id: int) -> None:
        self.redelivered.append(delivery_id)
        if delivery_id in self.failures:
            raise self.failures[delivery_id]


def run(hooks: FakeDeliveries, vcs: FakeVcs | None = None, *, monotonic=lambda: 0.0) -> int:
    return redeliver_failed_webhooks(
        hooks,
        vcs or FakeVcs(),
        now=NOW,
        policy=POLICY,
        monotonic=monotonic,
        budget_seconds=BUDGET,
        known_stale=set(),
    )


def test_only_failed_deliveries_get_an_attempt() -> None:
    hooks = FakeDeliveries([delivery(3, "c", ago=timedelta(minutes=30)), delivery(2, "b", status_code=202), delivery(1, "a")])

    assert run(hooks) == 2

    assert hooks.redelivered == [1, 3]
    assert hooks.since == [NOW - REDELIVERY_WINDOW]


def test_a_current_pull_request_delivery_is_checked_against_the_pr_head() -> None:
    """Голова PR — свежими метаданными по installation из payload'а доставки."""
    hooks = FakeDeliveries([delivery(1, "a")])
    vcs = FakeVcs()

    assert run(hooks, vcs) == 1

    assert hooks.payloads_read == [1]
    assert vcs.calls == [("metadata", REPO_FULL_NAME, 6, INSTALLATION_ID)]
    assert hooks.redelivered == [1]


def test_a_stale_delivery_is_not_redelivered(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Push A упал, push B прошёл: повтор A откатил бы merge_request на старый коммит."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(logging.getLogger("app.application.webhook_redelivery"), "disabled", False)
    hooks = FakeDeliveries([delivery(1, "a", action="synchronize")])

    assert run(hooks) == 0

    assert hooks.redelivered == []
    assert "доставка устарела: голова PR уже другая" in caplog.text


def test_a_review_thread_delivery_is_redelivered_without_the_head_check() -> None:
    hooks = FakeDeliveries([delivery(1, "a", event="pull_request_review_thread", action="resolved")])
    vcs = FakeVcs()

    assert run(hooks, vcs) == 1

    assert hooks.payloads_read == []
    assert vcs.calls == []
    assert hooks.redelivered == [1]


@pytest.mark.parametrize("where", ["payload", "metadata", "redeliver"])
def test_a_transient_failure_ends_the_pass(where: str) -> None:
    """Лимит GitHub или сеть на любом из трёх запросов: дальше в этом проходе не долбим."""
    error = VcsTransientError("HTTP 429")
    hooks = FakeDeliveries(
        [delivery(2, "b", ago=timedelta(minutes=30)), delivery(1, "a")],
        failures={1: error} if where == "redeliver" else None,
        payload_failures={1: error} if where == "payload" else None,
    )
    vcs = FakeVcs(metadata_error=error if where == "metadata" else None)

    with pytest.raises(VcsTransientError):
        run(hooks, vcs)
    assert hooks.payloads_read == [1]
    assert 2 not in hooks.redelivered


@pytest.mark.parametrize("where", ["payload", "metadata", "redeliver"])
def test_another_failure_skips_only_that_delivery(where: str) -> None:
    error = VcsError("HTTP 422")
    hooks = FakeDeliveries(
        [delivery(2, "b", event="pull_request_review_thread", action="resolved", ago=timedelta(minutes=30)), delivery(1, "a")],
        failures={1: error} if where == "redeliver" else None,
        payload_failures={1: error} if where == "payload" else None,
    )
    vcs = FakeVcs(metadata_error=error if where == "metadata" else None)

    assert run(hooks, vcs) == 1
    assert hooks.redelivered[-1] == 2


def test_a_delivery_without_a_pull_request_in_its_payload_is_skipped() -> None:
    hooks = FakeDeliveries([delivery(1, "a")])
    hooks.delivery_payload = lambda delivery_id: None  # type: ignore[method-assign]

    assert run(hooks) == 0
    assert hooks.redelivered == []


def test_a_delivery_of_an_outside_author_is_not_redelivered(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Приём ответил бы `ignored`: повтор только потратил бы запрос к GitHub."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(logging.getLogger("app.application.webhook_redelivery"), "disabled", False)
    hooks = FakeDeliveries([delivery(1, "a")])
    payload = webhook_payload("opened")
    payload["pull_request"]["author_association"] = "NONE"
    payload["pull_request"]["head"]["repo"] = {"id": 1, "full_name": "stranger/dmc-268-api-t1", "fork": True}
    hooks.delivery_payload = lambda delivery_id: payload  # type: ignore[method-assign]
    vcs = FakeVcs()

    assert run(hooks, vcs) == 0

    assert (hooks.redelivered, vcs.calls) == ([], [])
    assert "не запускает ревью (действие или автор)" in caplog.text


def test_the_pass_stops_when_its_time_budget_is_spent(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Часы — монотонные и подменены: каждый вызов на 12 с позже предыдущего."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(logging.getLogger("app.application.webhook_redelivery"), "disabled", False)
    ticks = iter(float(12 * n) for n in range(100))
    hooks = FakeDeliveries(
        [delivery(n, f"g{n}", event="pull_request_review_thread", action="resolved", ago=timedelta(minutes=100 - n)) for n in range(1, 6)]
    )

    # Старт — 0 с; перед доставками 12 и 24 с — в бюджете, 36 с — нет.
    assert run(hooks, monotonic=lambda: next(ticks)) == 2

    assert hooks.redelivered == [1, 2]
    assert "бюджет прохода повтора доставок исчерпан" in caplog.text


def test_nothing_to_redeliver_makes_no_attempts() -> None:
    hooks = FakeDeliveries([delivery(1, "a", status_code=202)])
    assert run(hooks) == 0
    assert hooks.redelivered == []


def test_stale_deliveries_do_not_crowd_out_a_fresh_one() -> None:
    """11 старых устаревших кандидатов и один свежий при лимите 10 переотправок.

    Лимит считает переотправки, а не кандидатов: свежий уходит в первом же
    проходе. Устаревшие запоминаются по `guid` и во втором проходе не стоят
    ни одного запроса.
    """
    stale = [
        delivery(n, f"stale-{n}", action="synchronize", ago=timedelta(hours=20) - timedelta(minutes=n))
        for n in range(1, 12)
    ]
    fresh = delivery(99, "fresh", ago=timedelta(minutes=30))
    hooks = FakeDeliveries([*stale, fresh])
    vcs = FakeVcs()
    known_stale: set[str] = set()

    first = redeliver_failed_webhooks(
        hooks, vcs, now=NOW, policy=POLICY, monotonic=lambda: 0.0, budget_seconds=BUDGET, known_stale=known_stale
    )

    assert first == 1
    assert hooks.redelivered == [99]
    assert known_stale == {f"stale-{n}" for n in range(1, 12)}

    hooks.payloads_read.clear()
    vcs.calls.clear()
    redeliver_failed_webhooks(
        hooks, vcs, now=NOW, policy=POLICY, monotonic=lambda: 0.0, budget_seconds=BUDGET, known_stale=known_stale
    )

    assert hooks.payloads_read == [99]
    assert len(vcs.calls) == 1


def _pass(hooks: FakeDeliveries, vcs: FakeVcs, known_stale: set[str]) -> int:
    return redeliver_failed_webhooks(
        hooks, vcs, now=NOW, policy=POLICY, monotonic=lambda: 0.0, budget_seconds=BUDGET, known_stale=known_stale
    )


def test_a_guid_that_left_the_list_is_forgotten() -> None:
    """Память не растёт: `guid`, которого больше нет среди кандидатов (вышел из окна), удаляется."""
    known_stale = {"gone", "a"}
    hooks = FakeDeliveries([delivery(1, "a", action="synchronize")])

    _pass(hooks, FakeVcs(), known_stale)

    assert known_stale == {"a"}


def test_a_permanent_failure_is_remembered_and_costs_nothing_next_pass() -> None:
    """404 на метаданных (PR удалён): во втором проходе к кандидату ни одного запроса."""
    hooks = FakeDeliveries([delivery(1, "a")])
    vcs = FakeVcs(metadata_error=VcsError("HTTP 404"))
    known_stale: set[str] = set()

    assert _pass(hooks, vcs, known_stale) == 0
    assert known_stale == {"a"}

    hooks.payloads_read.clear()
    vcs.calls.clear()
    assert _pass(hooks, vcs, known_stale) == 0
    assert (hooks.payloads_read, vcs.calls, hooks.redelivered) == ([], [], [])
