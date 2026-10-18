"""Метрики нагрузочного прогона - чистые функции, без стенда."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from .scenario import (
    Delivery,
    LoadProfile,
    LoadResult,
    RunTiming,
    percentile,
    summarize,
)

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(("q", "expected"), [(50, 2.0), (95, 4.0), (99, 4.0), (100, 4.0), (1, 1.0)])
def test_percentile_is_a_nearest_rank_sample_value(q: float, expected: float) -> None:
    assert percentile([4.0, 1.0, 3.0, 2.0], q) == expected


def test_percentile_of_nothing_is_absent() -> None:
    assert percentile([], 95) is None


def _result(**overrides) -> LoadResult:
    fields: dict[str, Any] = {
        "profile": LoadProfile(pull_requests=2, redeliveries=1),
        "deliveries": (
            Delivery(1, False, 202, 0.010, "run-1"),
            Delivery(2, False, 202, 0.030, "run-2"),
            Delivery(1, True, 202, 0.020, "run-1"),
        ),
        "runs": (
            RunTiming(1, "completed", T0, T0 + timedelta(seconds=1), 0.5),
            RunTiming(2, "completed", T0, T0 + timedelta(seconds=3), 0.5),
        ),
        "reviews_per_pr": {1: 1, 2: 1},
        "unfinished_runs": 0,
        "queue_left": 0,
        "dlq_left": 0,
        "intake_seconds": 0.5,
        "drain_seconds": 3.5,
    }
    return LoadResult(**(fields | overrides))


def test_summary_of_a_clean_run() -> None:
    summary = summarize(_result())

    assert summary["status_codes"] == {202: 3}
    assert summary["server_errors"] == 0
    assert summary["intake_latency_seconds"] == {
        "p50": 0.020,
        "p95": 0.030,
        "p99": 0.030,
        "max": 0.030,
    }
    assert summary["intake_throughput_per_second"] == pytest.approx(6.0)
    assert summary["end_to_end_seconds"]["p50"] == 1.0
    assert summary["end_to_end_seconds"]["max"] == 3.0
    assert summary["runs_by_status"] == {"completed": 2}
    assert summary["pull_requests_with_extra_runs"] == []
    assert summary["pull_requests_without_one_review_per_run"] == []


def test_summary_flags_a_redelivery_that_created_a_second_run_and_a_missing_review() -> None:
    result = _result(
        deliveries=(
            Delivery(1, False, 202, 0.010, "run-1"),
            Delivery(1, True, 202, 0.010, "run-1-bis"),
            Delivery(2, False, 500, 0.010, None),
        ),
        reviews_per_pr={1: 2},
    )

    summary = summarize(result)

    assert summary["server_errors"] == 1
    assert summary["pull_requests_with_extra_runs"] == [1]
    assert summary["pull_requests_without_one_review_per_run"] == [2]
