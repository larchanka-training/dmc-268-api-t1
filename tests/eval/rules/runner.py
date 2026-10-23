"""Прогон корпуса через сквозной стенд: каждый кейс - PR в каждой конфигурации правил.

Модель передаётся снаружи: живая в `test_rules_eval.py`, оракул разметки в
`test_runner.py`. Метрики считаются по тому, что дошло до PR: замечания
прогона из базы, сверенные с inline-комментариями в GitHub.
"""

import hashlib
from collections.abc import Sequence

from fastapi.testclient import TestClient

from app.application.ports.llm_gateway import LlmGateway
from app.domain.entities import Finding
from app.domain.enums import DiffSide, ReviewRunStatus
from app.domain.repo_rules import RULES_PATH
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR

from ...e2e.stack import Stack, deliver
from ..report import RunResult
from ..scoring import Reported, score_case
from .corpus import CONFIGS, Case


def prompts_sha256() -> str:
    """Хэш текстов промптов: метрики сравнимы только при одном промпте (LLM_EVAL §5)."""
    digest = hashlib.sha256()
    for path in sorted(DEFAULT_PROMPTS_DIR.glob("*.md")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _sha(*parts: object) -> str:
    return hashlib.sha1("/".join(map(str, parts)).encode()).hexdigest()


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
        base_sha = _sha("base", config)
        for case in cases:
            for repetition in range(1, repetitions + 1):
                number += 1
                pull = stack.github.open_pull(
                    number,
                    base_sha=base_sha,
                    head_sha=_sha("head", config, case.id, repetition),
                    base_files=base_files,
                    head_files=dict(case.files),
                    title=case.pr_title,
                    body=case.pr_description,
                )
                response = deliver(client, pull)
                assert response.status_code == 202, response.text
                run_id = response.json()["id"]
                stack.work(model, 1, timeout=timeout)
                results.append(_result(stack, config, case, repetition, number, run_id))
    return results


def _result(
    stack: Stack, config: str, case: Case, repetition: int, number: int, run_id: str
) -> RunResult:
    run = stack.run(run_id)
    if run.status is not ReviewRunStatus.COMPLETED:
        return RunResult(
            config, case.id, repetition, run.status.value, run.failure_reason, (), None
        )
    reported = tuple(_reported(f) for f in stack.findings(run_id))
    published = sum(len(r.comments) for r in stack.github.reviews_of(number))
    # Замечание без inline-комментария в PR пользователь не увидел бы: это сбой стенда.
    assert published == len(reported), (
        f"{case.id}/{config}: в базе {len(reported)}, в PR {published}"
    )
    return RunResult(
        config,
        case.id,
        repetition,
        run.status.value,
        None,
        reported,
        score_case(case.expected, reported),
    )
