"""What a Result says, including the awkward success."""

import pytest

from app.domain.result import Result


def test_unwrap_returns_the_value() -> None:
    assert Result.success(42).unwrap() == 42


def test_unwrap_raises_value_error_on_failure() -> None:
    with pytest.raises(ValueError, match="no good"):
        Result.failure("no good").unwrap()


def test_a_success_carrying_none_is_still_a_success() -> None:
    """Failure is decided by `error`, not by the value being empty."""
    assert Result.success(None).unwrap() is None


def test_ok_reads_the_error_not_the_value() -> None:
    assert Result.success(None).ok is True
    assert Result.failure("boom").ok is False
