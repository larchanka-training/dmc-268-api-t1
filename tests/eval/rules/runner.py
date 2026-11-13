"""Прогон корпуса через сквозной стенд: каждый кейс - PR в каждой конфигурации правил.

Модель передаётся снаружи: живая в `test_rules_eval.py`, оракул разметки в
`test_runner.py`. Метрики считаются по тому, что дошло до PR: замечания
прогона из базы, сверенные с inline-комментариями в GitHub. Доля валидных
ответов модели - по шагам прогона из базы: каждая попытка вызова - свой шаг.

Сбой одного прогона (не 202, таймаут воркера, замечаний в базе и в PR не
поровну) не роняет eval: прогон пишется со статусом `error` и текстом сбоя, и
корпус идёт дальше - оплаченные ответы модели не теряются, отчёт выходит.
"""

from collections.abc import Sequence
from contextlib import suppress

from fastapi.testclient import TestClient

from app.application.ports.llm_gateway import LlmGateway
from app.domain.entities import Finding
from app.domain.enums import DiffSide, ReviewRunStatus
from app.domain.repo_rules import RULES_PATH

from ...e2e.stack import Stack, deliver
from ..report import RunResult
from ..scoring import (
    Reported,
    calls_of,
    check_published,
    classify_calls,
    failure_text,
    pull_sha,
    score_case,
)
from .corpus import CONFIGS, Case


def _reported(finding: Finding) -> Reported:
    anchor = finding.anchor
    line = anchor.new_line if anchor.side is DiffSide.NEW else anchor.old_line
    assert line is not None
    return Reported(
        path=anchor.file_path,
        side=anchor.side.value,
        line=line,
        category=finding.category.value,
        rule=(finding.rule.name, finding.rule.check) if finding.rule else None,
    )


def run_corpus(
    stack: Stack,
    model: LlmGateway,
    cases: Sequence[Case],
    configs: Sequence[str],
    repetitions: int,
    *,
    timeout: float,
) -> list[RunResult]:
    client = TestClient(stack.api())
    results: list[RunResult] = []
    number = 0
    for config in configs:
        rules_text = CONFIGS[config]
        base_files = {} if rules_text is None else {RULES_PATH: rules_text}
        base_sha = pull_sha("base", config)
        for case in cases:
            for repetition in range(1, repetitions + 1):
                number += 1
                run_id: str | None = None
                try:
                    pull = stack.github.open_pull(
                        number,
                        base_sha=base_sha,
                        head_sha=pull_sha("head", config, case.id, repetition),
                        base_files=base_files,
                        head_files=dict(case.files),
                        title=case.pr_title,
                        body=case.pr_description,
                    )
                    response = deliver(client, pull)
                    assert response.status_code == 202, response.text
                    run_id = response.json()["id"]
                    stack.work(model, 1, timeout=timeout)
                    result = _result(stack, config, case, repetition, number, run_id)
                except Exception as error:  # noqa: BLE001 - сбой одного прогона пишется в отчёт
                    result = _failed(stack, config, case, repetition, run_id, error)
                    # Сообщение упавшего прогона могло остаться в очереди - воркер упал, не
                    # забрав его, или вернул его по таймауту: следующий кейс не должен его
                    # забрать. Брокер недоступен - следующие прогоны упадут и запишутся так
                    # же, отчёт всё равно выйдет.
                    with suppress(Exception):
                        stack.purge_queues()
                results.append(result)
    return results


def _failed(
    stack: Stack,
    config: str,
    case: Case,
    repetition: int,
    run_id: str | None,
    error: Exception,
) -> RunResult:
    """Прогон, упавший в обвязке (не 202, таймаут воркера, расхождение с PR), - в отчёт."""
    outputs = calls_of(None if run_id is None else lambda: stack.steps(run_id))
    return RunResult(
        config, case.id, repetition, "error", failure_text(error), (), None, outputs
    )


def _result(
    stack: Stack, config: str, case: Case, repetition: int, number: int, run_id: str
) -> RunResult:
    run = stack.run(run_id)
    outputs = classify_calls(stack.steps(run_id))
    if run.status is not ReviewRunStatus.COMPLETED:
        return RunResult(
            config, case.id, repetition, run.status.value, run.failure_reason, (), None, outputs
        )
    reported = tuple(_reported(f) for f in stack.findings(run_id))
    check_published(stack.github.reviews_of(number), len(reported), f"{case.id}/{config}")
    return RunResult(
        config,
        case.id,
        repetition,
        run.status.value,
        None,
        reported,
        score_case(case.expected, reported),
        outputs,
    )
