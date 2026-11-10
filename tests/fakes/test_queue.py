"""Фейковый канал очереди: `current` указывает на доставку, которую обрабатывает цикл."""

from types import SimpleNamespace

from app.worker.factory import Worker

from .queue import FakeChannel, FakeConnection


def _delivery(tag: int, body: bytes) -> tuple[SimpleNamespace, SimpleNamespace, bytes]:
    return SimpleNamespace(delivery_tag=tag, redelivered=False), SimpleNamespace(headers={}, type=None), body


def test_current_is_the_delivery_being_handled() -> None:
    channel = FakeChannel([_delivery(1, b"a"), _delivery(2, b"b"), _delivery(3, b"c")])
    seen: list[tuple[int | None, bytes]] = []

    def handle(body: bytes, kind: str | None) -> None:
        seen.append((channel.current, body))
        if body == b"b":
            raise ValueError("сломанное сообщение")

    Worker(connection=FakeConnection(), channel=channel).run(handle)  # type: ignore[arg-type]

    assert seen == [(0, b"a"), (1, b"b"), (2, b"c")]
    assert (channel.acked, channel.nacked) == ([1, 3], [2])
