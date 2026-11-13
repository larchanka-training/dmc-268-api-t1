"""Обвязка живого бенчмарка золотого набора на стенде с моделью-оракулом.

Живая модель платная, поэтому её прогон - вручную (`test_live_gold.py`). Здесь
проверяется, что `run_gold` доносит кейс до модели и обратно без потерь: описание
PR доходит до промпта, pass1 равен ответу модели, pass2 - то, что прошло отсев и
верификатор, а сбой кейса пишется заглушкой и не роняет набор. Артефакт обязан
грузиться раннером офлайн-eval - против мини-датасета из выбранных кейсов, не
полного набора: у полного не было бы ответов на остальные кейсы.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.application.llm_schema import ReviewOutput, VerificationOutput
from app.application.ports.llm_gateway import LlmRequest, LlmResponse, LlmTask
from app.domain.llm_errors import LlmError

from ..conftest import requires_broker, requires_db
from ..e2e.stack import Stack
from .dataset import DATASET_DIR, GoldCase, load_dataset
from .live import run_gold, write_artifact
from .runner import EXIT_DATASET_ERROR
from .runner import main as runner_main

pytestmark = [pytest.mark.integration, requires_db, requires_broker]

BUG = "TC-01-sql-injection"
FAILING = "TC-02-hardcoded-secret"
NEGATIVE = "TC-09-cosmetic-refactor"
OLD_SIDE = "TC-25-removed-owner-check"
INJECTED = "ADV-08-pr-description-instruction"
# Путь `docs/руководство.md` git пишет в кавычках с восьмеричными escape.
QUOTED_PATH = "TC-14-binary-rename-non-utf8"
SELECTED = (BUG, FAILING, NEGATIVE, OLD_SIDE, QUOTED_PATH, f"adversarial/{INJECTED}")
# Верификатор отбрасывает второе замечание TC-25 (correctness на той же строке).
DROPPED = (OLD_SIDE, "F2")
# На BUG оракул добавляет к ответу замечание вне диффа: `screen_findings` его отсеивает.
OFF_DIFF = {
    "filePath": "app/api/users.py",
    "side": "new",
    "line": 900,
    "category": "readability",
    "severity": "low",
    "message": "Строки 900 в диффе нет.",
    "confidence": None,
}
# На QUOTED_PATH оракул добавляет замечание к файлу с кириллицей в имени: GitHub его
# принимает, значит, и публикация ревью не должна падать.
NON_ASCII = {
    "filePath": "docs/руководство.md",
    "side": "new",
    "line": 9,
    "category": "readability",
    "severity": "low",
    "message": "В строке нет пробела после запятой.",
    "confidence": None,
}
_CANDIDATE = re.compile(r"^### (F\d+)$", re.MULTILINE)


class _GoldOracle:
    """Модель-оракул: на ревью - зафиксированный ответ кейса, на верификации - `keep`.

    Кейс узнаётся по пути любого его файла в сообщении с кодом: у TC-14 первый
    файл - переименование без hunk'ов, в промпт он не попадает. На `FAILING` ревью
    бросает `LlmError`, на `BUG` добавляет к ответу `OFF_DIFF`, на `DROPPED`
    верификатор отбрасывает кандидата. Сообщения
    с кодом запросов ревью запоминаются - по ним видно, что дошло до промпта.
    """

    def __init__(self, cases: tuple[GoldCase, ...]) -> None:
        self._cases = cases
        self.review_prompts: dict[str, str] = {}

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        code = request.messages[2].content
        (case,) = [
            c for c in self._cases
            if any(f"Файл {json.dumps(f.file_path)}" in code for f in c.files)
        ]
        if request.task is LlmTask.REVIEW:
            self.review_prompts[case.case_id] = code
            if case.case_id == FAILING:
                raise LlmError("модель недоступна")
            content = request.response_model.model_validate(_answer(case))
        else:
            verdicts = [
                {
                    "id": candidate,
                    "verdict": "drop" if (case.case_id, candidate) == DROPPED else "keep",
                    "reason": "оракул",
                }
                for candidate in _CANDIDATE.findall(request.messages[3].content)
            ]
            content = request.response_model.model_validate(
                {"schemaVersion": "1", "verdicts": verdicts}
            )
        return LlmResponse(model="oracle", content=content)


def _answer(case: GoldCase) -> dict[str, Any]:
    """Ответ оракула на ревью кейса: фикстура, у `BUG` - ещё и `OFF_DIFF`, у `QUOTED_PATH` - `NON_ASCII`."""
    answer: dict[str, Any] = json.loads(case.responses[0].text)
    if case.case_id == BUG:
        answer["findings"].append(OFF_DIFF)
    if case.case_id == QUOTED_PATH:
        answer["findings"].append(NON_ASCII)
    return answer


def _mini_dataset(root: Path) -> tuple[GoldCase, ...]:
    for case in SELECTED:
        shutil.copytree(DATASET_DIR / case, root / case)
    return load_dataset(root)


def _sorted(output: ReviewOutput) -> ReviewOutput:
    """Тот же ответ с находками по порядку якоря: порядок из базы не обязан совпадать."""
    findings = sorted(output.findings, key=lambda f: (f.file_path, f.side, f.line, f.category))
    return output.model_copy(update={"findings": tuple(findings)})


def test_run_gold_writes_an_artifact_the_offline_runner_loads(
    stack: Stack, tmp_path: Path
) -> None:
    mini = tmp_path / "dataset"
    cases = _mini_dataset(mini)
    oracle = _GoldOracle(cases)

    runs = run_gold(stack, oracle, cases, 1, timeout=60)
    out = tmp_path / "artifact"
    write_artifact(out, runs, {"model": "oracle", "repetitions": 1})

    statuses = {r.case_id: r.status for r in runs}
    assert [r.case_id for r in runs] == [c.case_id for c in cases]
    assert statuses == {
        BUG: "completed",
        FAILING: "failed",
        NEGATIVE: "completed",
        OLD_SIDE: "completed",
        QUOTED_PATH: "completed",
        INJECTED: "completed",
    }, [(r.case_id, r.failure) for r in runs]

    # Комментарий к файлу с кириллицей в имени опубликован по настоящему пути.
    published = {
        (c.path, c.line)
        for number in stack.github.pulls
        for review in stack.github.reviews_of(number)
        for c in review.comments
    }
    assert ("docs/руководство.md", 9) in published

    # Описание PR из mr.json дошло до промпта ревью: injection в нём проверяется на деле.
    assert "ADV-08-OBEYED-8e41" in oracle.review_prompts[INJECTED]
    assert "Описание PR" in oracle.review_prompts[INJECTED]

    by_case = {c.case_id: c for c in cases}
    for case_id in (BUG, NEGATIVE, OLD_SIDE, QUOTED_PATH, INJECTED):
        fixture = ReviewOutput.model_validate_json(by_case[case_id].responses[0].text)
        answer = ReviewOutput.model_validate(_answer(by_case[case_id]))
        pass1 = ReviewOutput.model_validate_json((out / "pass1" / case_id / "run-1.json").read_text())
        pass2 = ReviewOutput.model_validate_json((out / "pass2" / case_id / "run-1.json").read_text())
        # pass1 - ответ модели до отсева: у BUG в нём и замечание вне диффа.
        assert pass1 == answer, case_id
        # pass2 - без отсеянного (`OFF_DIFF` у BUG) и без отброшенного верификатором (F2 TC-25).
        kept = fixture.findings[:1] if case_id == OLD_SIDE else fixture.findings
        if case_id == QUOTED_PATH:
            kept = answer.findings
        assert _sorted(pass2) == _sorted(fixture.model_copy(update={"findings": kept})), case_id

    # Упавший кейс - заглушка, не проходящая схему, и статус с причиной.
    for snapshot in ("pass1", "pass2"):
        stub = json.loads((out / snapshot / FAILING / "run-1.json").read_text())
        assert list(stub) == ["liveRunFailed"] and stub["liveRunFailed"]

    verdicts = VerificationOutput.model_validate_json(
        (out / "verdicts" / OLD_SIDE / "run-1.json").read_text()
    )
    assert [(v.id, v.verdict.value) for v in verdicts.verdicts] == [("F1", "keep"), ("F2", "drop")]
    # Без кандидатов верификатор не вызывался - и вердиктов нет.
    assert not (out / "verdicts" / NEGATIVE).exists()

    timings = {t["case_id"]: t for t in json.loads((out / "timings.json").read_text())}
    assert timings[OLD_SIDE]["verifier_dropped_findings"] == 1
    assert timings[OLD_SIDE]["verification"] == "verified"
    assert {"analysing", "verifying"} <= set(timings[OLD_SIDE]["steps"])
    assert timings[OLD_SIDE]["duration_seconds"] > 0
    assert timings[OLD_SIDE]["outputs"] == {
        "first_try": 2, "after_retry": 0, "invalid": 0, "no_answer": 0
    }
    cases_with_answer = (BUG, NEGATIVE, OLD_SIDE, QUOTED_PATH, INJECTED)
    assert {c: timings[c]["rejected_findings"] for c in cases_with_answer} == {
        BUG: 1, NEGATIVE: 0, OLD_SIDE: 0, QUOTED_PATH: 0, INJECTED: 0
    }
    assert timings[FAILING]["status"] == "failed"
    assert timings[FAILING]["failure"]
    assert json.loads((out / "meta.json").read_text()) == {"model": "oracle", "repetitions": 1}

    # Раннер офлайн-eval читает оба снимка мини-датасета без ошибки датасета.
    for snapshot in ("pass1", "pass2"):
        assert len(load_dataset(mini, responses=out / snapshot)) == len(SELECTED)
        report = tmp_path / f"{snapshot}-report"
        code = runner_main(
            ["--dataset", str(mini), "--responses", str(out / snapshot), "--report-dir", str(report)]
        )
        assert code != EXIT_DATASET_ERROR, snapshot
        assert (report / "report.json").is_file()
