"""Регрессия инкрементального ревью: новый коммит не дублирует замечания к неизменённым строкам (#222).

Подписанный вебхук → API → база → очередь → воркер → модель → GitHub, несколько
push в один PR. Каждый push меняет код в другом месте: строка со старым
замечанием остаётся прежней, но может сдвинуться. Модель на каждом прогоне
снова находит старую проблему - как настоящая модель, которая видит файл целиком.
GitHub переносит тред на новую строку (`FakeGitHub.moved_threads`), отсев по
треду сверяет текущую строку треда с GitHub.
"""

import pytest
from fastapi.testclient import TestClient

from app.domain.enums import ReviewRunStatus
from app.infrastructure.queue.rabbitmq import DLQ_NAME

from ..conftest import requires_broker, requires_db
from ..fakes.github_api import PullRequest
from ..fakes.llm_gateway import (
    ScriptedLlmGateway,
    finding_json,
    review_response,
    verification_response,
)
from .stack import Stack, deliver

pytestmark = [pytest.mark.integration, requires_db, requires_broker]

BASE = "e0" * 20
PR = 31
PATH = "app/payments.py"
OLD_PROBLEM = "Отказ бросается исключением."

# Коммит 1: проблема на строке 3.
V1 = """\
def charge(amount, balance):
    if amount > balance:
        raise ValueError("недостаточно средств")
    return balance - amount
"""
# Коммит 2: над функцией добавлены импорт и пустая строка - строка с raise
# не изменилась, но съехала на 5.
V2 = """\
import logging

def charge(amount, balance):
    if amount > balance:
        raise ValueError("недостаточно средств")
    return balance - amount
"""
# Коммит 3: ниже добавлена новая функция с новой проблемой (строка 9).
V3 = V2 + """\

def refund(amount):
    return float(amount) * 1.0
"""


def _push(stack: Stack, head_sha: str, code: str) -> PullRequest:
    return stack.github.open_pull(PR, base_sha=BASE, head_sha=head_sha, head_files={PATH: code})


def _old(line: int, severity: str = "critical") -> dict[str, object]:
    return finding_json(PATH, line, OLD_PROBLEM, category="correctness", severity=severity)


def _review(stack: Stack, pull: PullRequest, action: str, *findings: dict[str, object]) -> str:
    response = deliver(TestClient(stack.api()), pull, action=action)
    assert response.status_code == 202, response.text
    run_id: str = response.json()["id"]
    verdicts = [(f"F{i}", "keep") for i in range(1, len(findings) + 1)]
    stack.work(ScriptedLlmGateway([review_response(*findings), verification_response(*verdicts)]), 1)
    assert stack.run(run_id).status is ReviewRunStatus.COMPLETED
    assert stack.queue_depth(DLQ_NAME) == 0
    return run_id


def _comments(stack: Stack) -> list[list[tuple[str, int]]]:
    """Inline-комментарии каждого ревью PR по порядку."""
    return [[(c.path, c.line) for c in r.comments] for r in stack.github.reviews_of(PR)]


def _move_first_thread(stack: Stack, line: int) -> None:
    (first, *_) = stack.github.reviews_of(PR)
    (comment,) = first.comments
    stack.github.moved_threads[f"T_{comment.id}"] = line


def test_commit_that_shifts_an_unchanged_line_does_not_repeat_its_finding(stack: Stack) -> None:
    """Строку с замечанием сдвинул коммит выше по файлу: второго комментария нет, gate - `failure`."""
    first_run = _review(stack, _push(stack, "e1" * 20, V1), "opened", _old(3))
    _move_first_thread(stack, 5)

    second_run = _review(stack, _push(stack, "e2" * 20, V2), "synchronize", _old(5))

    assert _comments(stack) == [[(PATH, 3)], []]
    (covered,) = stack.findings(second_run)
    (original,) = stack.findings(first_run)
    assert covered.covered_by_finding_id == original.id
    assert [c.conclusion for c in stack.github.check_runs] == ["failure", "failure"]


def test_new_problem_in_a_later_commit_is_published_alone(stack: Stack) -> None:
    """Третий коммит добавил новую проблему: публикуется только она, старая - ни разу больше."""
    _review(stack, _push(stack, "e1" * 20, V1), "opened", _old(3))
    _move_first_thread(stack, 5)
    _review(stack, _push(stack, "e2" * 20, V2), "synchronize", _old(5))

    new_problem = finding_json(PATH, 9, "Деньги во float.", category="correctness", severity="high")
    _review(stack, _push(stack, "e3" * 20, V3), "synchronize", _old(5), new_problem)

    assert _comments(stack) == [[(PATH, 3)], [], [(PATH, 9)]]


def test_lower_severity_restatement_of_the_old_finding_is_not_a_new_comment(stack: Stack) -> None:
    """Модель на новом коммите оценила ту же проблему мягче: комментарий не повторяется."""
    _review(stack, _push(stack, "e1" * 20, V1), "opened", _old(3))
    _move_first_thread(stack, 5)

    _review(stack, _push(stack, "e2" * 20, V2), "synchronize", _old(5, severity="medium"))

    assert _comments(stack) == [[(PATH, 3)], []]


def test_finding_on_a_line_the_commit_did_change_is_published_again(stack: Stack) -> None:
    """Контроль: строку с замечанием изменили (тред устарел) - замечание к новой версии строки
    публикуется, отсев не прячет проблему, которую не исправили."""
    _review(stack, _push(stack, "e1" * 20, V1), "opened", _old(3))
    (first,) = stack.github.reviews_of(PR)
    stack.github.outdated_threads.add(f"T_{first.comments[0].id}")
    changed = V1.replace('ValueError("недостаточно средств")', 'RuntimeError("мало денег")')

    _review(stack, _push(stack, "e2" * 20, changed), "synchronize", _old(3))

    assert _comments(stack) == [[(PATH, 3)], [(PATH, 3)]]
