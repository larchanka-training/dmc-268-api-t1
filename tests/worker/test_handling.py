from app.worker.handling import should_ack


def test_no_exception_means_ack() -> None:
    assert should_ack(None) is True


def test_any_exception_means_nack() -> None:
    assert should_ack(ValueError("boom")) is False
