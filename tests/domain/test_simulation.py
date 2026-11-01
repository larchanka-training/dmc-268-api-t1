"""Правила симуляции песочницы: запуск, переходы, допуск по лимитам, фрагмент как новый файл."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.diff_parser import parse_diff
from app.domain.entities import Simulation, SimulationResult
from app.domain.enums import SimulationSourceKind, VerificationOutcome
from app.domain.enums import SimulationStatus as S
from app.domain.simulation import (
    ACTIVE_RETRY_AFTER_SECONDS,
    SIMULATION_TERMINAL_STATUSES,
    Admitted,
    Limited,
    admit_simulation,
    advance_simulation,
    code_as_new_file,
    complete_simulation,
    fail_simulation,
    new_simulation,
)

from .conftest import NOW, uid

HOUR = timedelta(hours=1)
LATER = NOW + timedelta(seconds=30)
RESULT = SimulationResult(findings=(), discarded=(), context=())


@pytest.fixture
def simulation() -> Simulation:
    return new_simulation(
        simulation_id=uid(1),
        user_id=uid(2),
        source_kind=SimulationSourceKind.CODE,
        source="def f():\n    return 1\n",
        rules="## Errors\n",
        file_path="app/a.py",
        now=NOW,
        ttl=timedelta(days=1),
    )


def test_a_new_simulation_is_queued_and_expires_after_the_ttl(simulation: Simulation) -> None:
    assert simulation.status is S.QUEUED
    assert simulation.created_at == simulation.updated_at == NOW
    assert simulation.expires_at == NOW + timedelta(days=1)
    assert simulation.result is None
    assert simulation.error_code is None


def test_a_diff_simulation_has_no_file_path() -> None:
    diff = new_simulation(
        simulation_id=uid(1),
        user_id=uid(2),
        source_kind=SimulationSourceKind.DIFF,
        source="diff --git a/x b/x\n",
        rules="",
        file_path="ignored.py",
        now=NOW,
        ttl=HOUR,
    )
    assert diff.file_path is None


def test_analysis_steps_go_in_order_and_keep_the_input(simulation: Simulation) -> None:
    current = simulation
    for status in (S.BUILDING_CONTEXT, S.ANALYSING, S.VERIFYING):
        current = advance_simulation(current, status, LATER).unwrap()
        assert current.status is status
        assert current.updated_at == LATER
        assert (current.source, current.rules) == (simulation.source, simulation.rules)


def test_skipping_a_step_is_refused(simulation: Simulation) -> None:
    verdict = advance_simulation(simulation, S.ANALYSING, LATER)
    assert not verdict.ok
    assert "queued" in (verdict.error or "")


@pytest.mark.parametrize("terminal", sorted(SIMULATION_TERMINAL_STATUSES))
def test_terminal_statuses_are_not_set_by_advance(simulation: Simulation, terminal: S) -> None:
    verifying = replace(simulation, status=S.VERIFYING)
    assert not advance_simulation(verifying, terminal, LATER).ok


def test_completion_stores_the_result_and_erases_the_input(simulation: Simulation) -> None:
    verifying = replace(simulation, status=S.VERIFYING)
    done = complete_simulation(
        verifying,
        RESULT,
        model="glm-5.3",
        tokens_used=1200,
        verification=VerificationOutcome.VERIFIED,
        now=LATER,
    ).unwrap()
    assert done.status is S.COMPLETED
    assert (done.result, done.model, done.tokens_used) == (RESULT, "glm-5.3", 1200)
    assert done.verification is VerificationOutcome.VERIFIED
    assert (done.source, done.rules) == (None, None)
    assert done.file_path == "app/a.py"
    assert done.updated_at == LATER


def test_completion_before_verification_is_refused(simulation: Simulation) -> None:
    analysing = replace(simulation, status=S.ANALYSING)
    verdict = complete_simulation(
        analysing,
        RESULT,
        model="glm-5.3",
        tokens_used=1,
        verification=VerificationOutcome.VERIFIED,
        now=LATER,
    )
    assert not verdict.ok


@pytest.mark.parametrize("status", [S.QUEUED, S.BUILDING_CONTEXT, S.ANALYSING, S.VERIFYING])
def test_failure_from_any_step_keeps_the_code_and_tokens_and_erases_the_input(
    simulation: Simulation, status: S
) -> None:
    failed = fail_simulation(
        replace(simulation, status=status), "LLM_TIMEOUT", LATER, model="glm-5.3", tokens_used=40
    ).unwrap()
    assert failed.status is S.FAILED
    assert (failed.error_code, failed.model, failed.tokens_used) == ("LLM_TIMEOUT", "glm-5.3", 40)
    assert (failed.source, failed.rules, failed.result) == (None, None, None)


@pytest.mark.parametrize("terminal", sorted(SIMULATION_TERMINAL_STATUSES))
def test_a_finished_simulation_accepts_nothing(simulation: Simulation, terminal: S) -> None:
    """Повторная доставка и поздний итог воркера после перевода зависшей в `failed`."""
    finished = replace(simulation, status=terminal, source=None, rules=None)
    attempts = [
        advance_simulation(finished, S.BUILDING_CONTEXT, LATER),
        fail_simulation(finished, "SIMULATION_STALE", LATER),
        complete_simulation(
            finished,
            RESULT,
            model="glm-5.3",
            tokens_used=1,
            verification=VerificationOutcome.VERIFIED,
            now=LATER,
        ),
    ]
    assert all("terminal" in (attempt.error or "") for attempt in attempts)


def _admit(recent: list[timedelta], *, active: bool = False, limit: int = 3) -> Admitted | Limited:
    return admit_simulation(
        active=active,
        recent_created_at=[NOW - ago for ago in recent],
        now=NOW,
        max_per_window=limit,
        window=HOUR,
    )


def test_an_unfinished_simulation_blocks_the_next_one_for_the_poll_interval() -> None:
    assert _admit([], active=True) == Limited(ACTIVE_RETRY_AFTER_SECONDS, active=True)


def test_launches_under_the_limit_are_admitted() -> None:
    assert _admit([timedelta(minutes=1), timedelta(minutes=2)]) == Admitted()


def test_the_limit_waits_for_the_oldest_launch_to_leave_the_window() -> None:
    """Сценарий спеки: 60 запусков, самый старый - 50 минут назад, повтор через 600 с."""
    recent = [timedelta(minutes=50)] + [timedelta(minutes=1)] * 59
    assert _admit(recent, limit=60) == Limited(600, active=False)


def test_a_launch_exactly_one_window_ago_no_longer_counts() -> None:
    assert _admit([HOUR, timedelta(minutes=1), timedelta(minutes=2)]) == Admitted()


def test_retry_after_rounds_up_and_is_at_least_one_second() -> None:
    almost_out = HOUR - timedelta(milliseconds=200)
    assert _admit([almost_out, timedelta(minutes=1), timedelta(minutes=2)]) == Limited(1, active=False)
    with_fraction = HOUR - timedelta(seconds=90, milliseconds=500)
    assert _admit([with_fraction, timedelta(minutes=1), timedelta(minutes=2)]) == Limited(91, active=False)


def test_over_the_limit_waits_until_one_slot_frees() -> None:
    """Лимит снизили настройкой: в окне больше запусков, чем он позволяет."""
    recent = [timedelta(minutes=m) for m in (55, 40, 30, 20, 10)]
    assert _admit(recent, limit=3) == Limited(30 * 60, active=False)


def test_a_zero_limit_is_a_configuration_error() -> None:
    with pytest.raises(ValueError):
        _admit([], limit=0)


# Диффы ниже сняты с `git diff` нового файла с тем же текстом (git 2.x).
GIT_NEW_FILE = {
    "def f():\n    return 1\n": (
        "diff --git a/app/a.py b/app/a.py\nnew file mode 100644\nindex 0000000..b859599\n"
        "--- /dev/null\n+++ b/app/a.py\n@@ -0,0 +1,2 @@\n+def f():\n+    return 1\n"
    ),
    "x = 1\ny = 2": (
        "diff --git a/app/a.py b/app/a.py\nnew file mode 100644\nindex 0000000..dcfab70\n"
        "--- /dev/null\n+++ b/app/a.py\n@@ -0,0 +1,2 @@\n+x = 1\n+y = 2\n\\ No newline at end of file\n"
    ),
    "a = 1\r\nb = 2\r\n": (
        "diff --git a/app/a.py b/app/a.py\nnew file mode 100644\nindex 0000000..b4e31d4\n"
        "--- /dev/null\n+++ b/app/a.py\n@@ -0,0 +1,2 @@\n+a = 1\r\n+b = 2\r\n"
    ),
    "\n": (
        "diff --git a/app/a.py b/app/a.py\nnew file mode 100644\nindex 0000000..8b13789\n"
        "--- /dev/null\n+++ b/app/a.py\n@@ -0,0 +1 @@\n+\n"
    ),
}


@pytest.mark.parametrize("content", list(GIT_NEW_FILE), ids=["newline", "no-newline", "crlf", "empty-line"])
def test_a_fragment_is_the_new_file_of_a_pull_request(content: str) -> None:
    """Тот же вход - тот же анализ: фрагмент разобран так же, как новый файл в диффе PR."""
    assert [code_as_new_file("app/a.py", content)] == parse_diff(GIT_NEW_FILE[content])


def test_every_line_of_a_fragment_is_added() -> None:
    (hunk,) = code_as_new_file("app/billing.py", "".join(f"line {n}\n" for n in range(1, 13))).hunks
    assert hunk.text.splitlines()[0] == "@@ -0,0 +1,12 @@"
    assert hunk.changed_new_lines == frozenset(range(1, 13))
    assert hunk.changed_old_lines == frozenset()


def test_an_empty_fragment_has_nothing_to_review() -> None:
    assert code_as_new_file("app/a.py", "").hunks == ()
