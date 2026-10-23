"""Инварианты конвейера под нагрузкой - общие для малого и полного прогона."""

from typing import Any


def assert_consistent(summary: dict[str, Any], *, strict: bool = False) -> None:
    """Ни одного 5xx, повтор при активном прогоне схлопнут, ревью - по одному на прогон.

    `strict` - повторов после завершения быть не должно: воркеры ждали конца приёма.
    """
    assert summary["server_errors"] == 0, summary["status_codes"]
    assert set(summary["status_codes"]) == {202}, summary["status_codes"]
    assert summary["pull_requests_with_extra_runs"] == []
    assert summary["pull_requests_without_one_review_per_run"] == []
    if strict:
        assert summary["redeliveries_after_completion"] == []
    assert summary["unfinished_runs"] == 0
    assert set(summary["runs_by_status"]) == {"completed"}, summary["runs_by_status"]
    assert (summary["queue_left"], summary["dlq_left"]) == (0, 0)
