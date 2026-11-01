"""Ранг важности находки: сравнение с порогом."""

import pytest

from app.domain.enums import FindingSeverity, severity_at_least

_LOW, _MEDIUM, _HIGH, _CRITICAL = (
    FindingSeverity.LOW,
    FindingSeverity.MEDIUM,
    FindingSeverity.HIGH,
    FindingSeverity.CRITICAL,
)


@pytest.mark.parametrize(
    ("severity", "threshold", "expected"),
    [
        (_LOW, _LOW, True),
        (_LOW, _MEDIUM, False),
        (_LOW, _HIGH, False),
        (_LOW, _CRITICAL, False),
        (_MEDIUM, _LOW, True),
        (_MEDIUM, _MEDIUM, True),
        (_MEDIUM, _HIGH, False),
        (_MEDIUM, _CRITICAL, False),
        (_HIGH, _LOW, True),
        (_HIGH, _MEDIUM, True),
        (_HIGH, _HIGH, True),
        (_HIGH, _CRITICAL, False),
        (_CRITICAL, _LOW, True),
        (_CRITICAL, _MEDIUM, True),
        (_CRITICAL, _HIGH, True),
        (_CRITICAL, _CRITICAL, True),
    ],
)
def test_severity_is_at_least_threshold_by_rank(
    severity: FindingSeverity, threshold: FindingSeverity, expected: bool
) -> None:
    assert severity_at_least(severity, threshold) is expected
