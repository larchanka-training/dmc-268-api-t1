"""Живой прогон золотого набора через сквозной стенд: два снимка ответа и тайминги.

Каждый кейс - PR из его `diff.patch` и `mr.json` в фейке GitHub; воркер стенда
гоняет боевой конвейер с моделью снаружи: живой в `test_live_gold.py`, оракулом
в `test_live_runner.py`. На кейс снимаются два ответа в форме `ReviewOutput`,
которые раннер офлайн-eval читает опцией `--responses`:

- pass1 - ответ первого прохода модели как есть, до отсева (`RecordingLlmGateway`);
- pass2 - замечания прогона из базы, то есть после отсева и верификатора, со
  сводкой из pass1.

Сбой кейса (не 202, таймаут воркера, прогон не `completed`) не роняет набор:
в pass1 и pass2 ложится заглушка `{"liveRunFailed": ...}` - она не проходит
схему и честно входит в долю невалидных, а набор повторов у кейсов остаётся
одинаковым. Раскладка артефакта - design D3 change `add-live-gold-benchmark`.
"""

import json
import os
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.application.llm_schema import ReviewOutput, VerificationOutput
from app.application.ports.llm_gateway import (
    LlmGateway,
    LlmRequest,
    LlmResponse,
    LlmTask,
)
from app.config import Settings
from app.domain.entities import Finding, ParsedFile
from app.domain.enums import ReviewRunStatus
from app.infrastructure.container import llm_http_timeout
from app.infrastructure.llm.openai_compatible import OpenAiCompatibleGateway

from ..e2e.stack import Stack, deliver
from .dataset import GoldCase
from .scoring import (
    FAILURE_LIMIT,
    OutputCounts,
    calls_of,
    check_published,
    classify_calls,
    failure_text,
    pull_sha,
)


def live_gateway(settings: Settings) -> OpenAiCompatibleGateway:
    """Живая модель eval: EUrouter из настроек - модель, ключи, effort, попытки."""
    return OpenAiCompatibleGateway(
        httpx.Client(base_url=settings.eurorouter_base_url, timeout=llm_http_timeout(settings)),
        settings.eurorouter_model,
        settings.eurorouter_keys,
        name="Eurorouter",
        max_attempts=settings.llm_max_attempts,
        reasoning_effort=settings.eurorouter_reasoning_effort,
    )


def run_timeout(settings: Settings) -> float:
    """Таймаут воркера на прогон: два вызова модели, каждый - до таймаута на каждую попытку,
    и запас на остальное."""
    return 2 * settings.llm_timeout_seconds * settings.llm_max_attempts + 60


def eval_repetitions() -> int:
    """Число повторов живого eval: `EVAL_REPETITIONS`, по умолчанию 1."""
    return int(os.environ.get("EVAL_REPETITIONS") or 1)


class RecordingLlmGateway:
    """Декоратор модели: делегирует вызов и запоминает ответы обоих проходов.

    `review` - ответ первого прохода (`LlmTask.REVIEW`), `verification` -
    вердикты верификатора (`LlmTask.VERIFY`); сбой вызова ничего не запоминает.
    Один экземпляр - на один прогон.
    """

    def __init__(self, inner: LlmGateway) -> None:
        self._inner = inner
        self.review: ReviewOutput | None = None
        self.verification: VerificationOutput | None = None

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        response = self._inner.complete(request)
        content = response.content
        if request.task is LlmTask.REVIEW and isinstance(content, ReviewOutput):
            self.review = content
        elif request.task is LlmTask.VERIFY and isinstance(content, VerificationOutput):
            self.verification = content
        return response


@dataclass(frozen=True, slots=True)
class LiveRun:
    """Один прогон кейса: снимки ответа (JSON-текст) и строка `timings.json`."""

    case_id: str
    repetition: int
    status: str
    pass1: str
    pass2: str
    verdicts: str | None = None
    failure: str | None = None
    duration_seconds: float | None = None
    steps: dict[str, float] = field(default_factory=dict)
    tokens_used: int | None = None
    verification: str | None = None
    rejected_findings: int = 0
    verifier_dropped_findings: int = 0
    outputs: OutputCounts = field(default_factory=OutputCounts)

    def timing(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "repetition": self.repetition,
            "status": self.status,
            "failure": self.failure,
            "duration_seconds": self.duration_seconds,
            "steps": self.steps,
            "tokens_used": self.tokens_used,
            "verification": self.verification,
            "rejected_findings": self.rejected_findings,
            "verifier_dropped_findings": self.verifier_dropped_findings,
            "outputs": asdict(self.outputs),
        }


def run_gold(
    stack: Stack,
    model: LlmGateway,
    cases: Sequence[GoldCase],
    repetitions: int,
    *,
    timeout: float,
) -> list[LiveRun]:
    """Каждый кейс `repetitions` раз - отдельным PR через стенд; порядок - кейсы, затем повторы."""
    client = TestClient(stack.api())
    runs: list[LiveRun] = []
    number = 0
    for case in cases:
        for repetition in range(1, repetitions + 1):
            number += 1
            recorder = RecordingLlmGateway(model)
            run_id: str | None = None
            try:
                pull = stack.github.open_pull_from_diff(
                    number,
                    base_sha=pull_sha("base", case.case_id),
                    head_sha=pull_sha("head", case.case_id, repetition),
                    # Дифф как есть: номера строк эталонных якорей не сдвигаются.
                    diff=case.diff,
                    head_files=_added_files(case.files),
                    title=case.mr.title,
                    body=case.mr.description,
                    head_ref=case.mr.source_branch,
                    base_ref=case.mr.target_branch,
                )
                response = deliver(client, pull)
                assert response.status_code == 202, response.text
                run_id = response.json()["id"]
                stack.work(recorder, 1, timeout=timeout)
                run = _result(stack, case, repetition, number, run_id, recorder)
            except Exception as error:  # noqa: BLE001 - сбой одного прогона пишется в артефакт
                run = _failed(stack, case, repetition, run_id, failure_text(error))
                # Сообщение упавшего прогона могло остаться в очереди: следующий кейс не
                # должен его забрать (как в `tests/eval/rules/runner.py`).
                with suppress(Exception):
                    stack.purge_queues()
            runs.append(run)
    return runs


def write_artifact(directory: Path, runs: Sequence[LiveRun], meta: Mapping[str, Any]) -> None:
    """Раскладка D3: `pass1/`, `pass2/`, `verdicts/` по кейсам, `timings.json`, `meta.json`."""
    for run in runs:
        name = f"run-{run.repetition}.json"
        _write(directory / "pass1" / run.case_id / name, run.pass1)
        _write(directory / "pass2" / run.case_id / name, run.pass2)
        if run.verdicts is not None:
            _write(directory / "verdicts" / run.case_id / name, run.verdicts)
    _write(directory / "timings.json", _json([r.timing() for r in runs]))
    _write(directory / "meta.json", _json(dict(meta)))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _added_files(files: Sequence[ParsedFile]) -> dict[str, str]:
    """Содержимое добавленных файлов на head: hunk `-0,0` - файл целиком, строки `+`."""
    contents: dict[str, str] = {}
    for file in files:
        if not file.is_added or file.is_binary:
            continue
        lines: list[str] = []
        for hunk in file.hunks:
            body = hunk.text.split("\n")[1:]
            lines += [line[1:] for line in body if line.startswith("+")]
        contents[file.file_path] = "\n".join(lines) + "\n"
    return contents


def _stub(reason: str) -> str:
    return json.dumps({"liveRunFailed": reason}, ensure_ascii=False)


def _failed(
    stack: Stack, case: GoldCase, repetition: int, run_id: str | None, failure: str
) -> LiveRun:
    """Прогон, упавший в обвязке: заглушки в оба снимка и исходы вызовов, если шаги читаются."""
    outputs = calls_of(None if run_id is None else lambda: stack.steps(run_id))
    stub = _stub(failure)
    return LiveRun(case.case_id, repetition, "error", stub, stub, failure=failure, outputs=outputs)


def _result(
    stack: Stack,
    case: GoldCase,
    repetition: int,
    number: int,
    run_id: str,
    recorder: RecordingLlmGateway,
) -> LiveRun:
    run = stack.run(run_id)
    steps = stack.steps(run_id)
    # Повтор вызова модели - отдельный шаг того же этапа: время этапа - сумма его шагов.
    durations: dict[str, float] = {}
    for step in steps:
        if step.duration_seconds is not None:
            stage = step.stage.value
            durations[stage] = durations.get(stage, 0.0) + step.duration_seconds
    timing: dict[str, Any] = {
        "duration_seconds": run.duration_seconds,
        "steps": durations,
        "tokens_used": run.tokens_used,
        "verification": run.verification.value if run.verification else None,
        "rejected_findings": run.rejected_findings,
        "verifier_dropped_findings": run.verifier_dropped_findings,
        "outputs": classify_calls(steps),
    }
    verdicts = (
        recorder.verification.model_dump_json(by_alias=True)
        if recorder.verification is not None
        else None
    )
    if run.status is not ReviewRunStatus.COMPLETED:
        failure = (run.failure_reason or run.status.value)[:FAILURE_LIMIT]
        stub = _stub(failure)
        return LiveRun(
            case.case_id, repetition, run.status.value, stub, stub, verdicts, failure, **timing
        )
    # Прогон завершён, значит первый проход ответил: декоратор его записал.
    assert recorder.review is not None, f"{case.case_id}: нет ответа первого прохода"
    findings = stack.findings(run_id)
    # Расхождение с PR бросает и уходит в `except` `run_gold`: прогон - `error` (D4).
    check_published(stack.github.reviews_of(number), len(findings), case.case_id)
    # Модели провода принимают только camelCase-ключи - как ответ модели.
    pass2 = ReviewOutput.model_validate(
        {
            "schemaVersion": "1",
            "findings": [_finding_json(f) for f in findings],
            "summary": recorder.review.summary.model_dump(by_alias=True),
        }
    )
    return LiveRun(
        case.case_id,
        repetition,
        run.status.value,
        recorder.review.model_dump_json(by_alias=True),
        pass2.model_dump_json(by_alias=True),
        verdicts,
        **timing,
    )


def _finding_json(finding: Finding) -> dict[str, Any]:
    """Замечание прогона из базы - в форме ответа модели, как его читает раннер."""
    anchor = finding.anchor
    return {
        "filePath": anchor.file_path,
        "side": anchor.side.value,
        "line": anchor.line,
        "category": finding.category.value,
        "severity": finding.severity.value,
        "message": finding.message,
        "confidence": finding.confidence,
        "suggestion": None if finding.suggestion is None else {"diff": finding.suggestion},
        "rule": (
            None
            if finding.rule is None
            else {"name": finding.rule.name, "check": finding.rule.check}
        ),
    }
