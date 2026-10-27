"""Фейковый канал очереди: повторная доставка только после ack, как у брокера."""

from collections.abc import Callable
from types import SimpleNamespace

from app.worker.factory import Worker

from .queue import FakeChannel, FakeConnection


def _delivery(tag: int, body: bytes) -> tuple[SimpleNamespace, SimpleNamespace, bytes]:
    return SimpleNamespace(delivery_tag=tag), SimpleNamespace(headers={}), body


def _run(channel: FakeChannel, handler: Callable[[bytes], None]) -> list[bytes]:
    seen: list[bytes] = []

    def handle(body: bytes) -> None:
        seen.append(body)
        handler(body)

    Worker(connection=FakeConnection(), channel=channel).run(handle)  # type: ignore[arg-type]
    return seen


def test_an_acked_message_is_redelivered() -> None:
    channel = FakeChannel(
        [_delivery(1, b"a"), _delivery(2, b"a")], redelivery_of={1: 0}
    )

    seen = _run(channel, lambda body: None)

    assert seen == [b"a", b"a"]
    assert (channel.acked, channel.nacked, channel.skipped) == ([1, 2], [], [])


def test_a_nacked_message_is_not_redelivered() -> None:
    channel = FakeChannel(
        [_delivery(1, b"a"), _delivery(2, b"a"), _delivery(3, b"b")],
        redelivery_of={1: 0},
    )

    def fail_on_a(body: bytes) -> None:
        if body == b"a":
            raise ValueError("сломанное сообщение")

    seen = _run(channel, fail_on_a)

    # После nack сообщение в DLQ: повтора нет, следующее сообщение приходит.
    assert seen == [b"a", b"b"]
    assert (channel.acked, channel.nacked, channel.skipped) == ([3], [1], [1])
