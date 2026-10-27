"""Поддельный `LlmGateway`: заранее заданные ответы по одному на вызов и запись запросов.

Ответы задаются в форме провода (JSON моделей ответа), чтобы тест проходил
через ту же проверку формы, что и ответ настоящего провайдера.

Сценарий для песочницы - JSON-файл, `load_llm_script`. Порядок, в котором
прогон расходует его записи. Шлюз один на весь прогон, сообщения
обрабатываются по очереди, поэтому сценарий - одна лента на все PR, по записи
на вызов `complete`:

1. ревью PR (`LlmTask.REVIEW`, `request_review`) - всегда, ровно один вызов;
2. верификация (`LlmTask.VERIFY`, `request_verification`) - только если после
   доменного отсева остался хоть один кандидат; без кандидатов вызова нет, и
   следующая запись достаётся ревью следующего PR;
3. дальше - ревью следующего PR в порядке PR.

Повторов вне адаптера нет: повторы внутри вызова (`on_retry`) делает настоящий
адаптер, не этот фейк, а сообщение после сбоя уходит nack без повторной
доставки. Поэтому запись `error` расходуется ровно один раз: на ревью она
роняет прогон (`failed`), на верификации - fail-open (`unavailable`, кандидаты
публикуются как есть). Кандидаты верификатора - `F1`, `F2`, … в порядке после
отсева. Записи кончились - `AssertionError` на лишнем вызове.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeIs

from pydantic import BaseModel, ValidationError

from app.application.llm_schema import ReviewOutput, SummaryOutput, VerificationOutput
from app.application.ports.llm_gateway import LlmRequest, LlmResponse
from app.domain.enums import DiffSide, FindingCategory, FindingSeverity, VerifierVerdict
from app.domain.llm_errors import (
    LlmError,
    LlmOutputError,
    LlmRateLimitedError,
    LlmTimeoutError,
)

SUMMARY = {"problem": "Есть замечания.", "positives": [], "effort": "час"}


def finding_json(
    path: str,
    line: int,
    message: str,
    *,
    rule: tuple[str, int] | None = None,
    category: str = "readability",
    severity: str = "low",
    side: str = "new",
) -> dict[str, Any]:
    return {
        "filePath": path,
        "side": side,
        "line": line,
        "category": category,
        "severity": severity,
        "message": message,
        "confidence": None,
        "rule": {"name": rule[0], "check": rule[1]} if rule else None,
    }


def review_response(
    *findings: dict[str, Any],
    tokens: int = 0,
    model: str = "m",
    thread_follow_ups: tuple[dict[str, Any], ...] = (),
) -> LlmResponse[ReviewOutput]:
    payload: dict[str, Any] = {"schemaVersion": "1", "findings": list(findings), "summary": SUMMARY}
    if thread_follow_ups:
        payload["threadFollowUps"] = list(thread_follow_ups)
    content = ReviewOutput.model_validate(payload)
    return LlmResponse(model=model, tokens_used=tokens, content=content)


def verification_response(
    *verdicts: tuple[str, str], tokens: int = 0, model: str = "m"
) -> LlmResponse[VerificationOutput]:
    content = VerificationOutput.model_validate(
        {
            "schemaVersion": "1",
            "verdicts": [
                {"id": vid, "verdict": verdict, "reason": f"причина {vid}"} for vid, verdict in verdicts
            ],
        }
    )
    return LlmResponse(model=model, tokens_used=tokens, content=content)


@dataclass
class ScriptedLlmGateway:
    """Отвечает следующим ответом из списка; исключение в списке - бросает его."""

    answers: list[LlmResponse[Any] | Exception]
    requests: list[LlmRequest[Any]] = field(default_factory=list)

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        self.requests.append(request)
        if not self.answers:
            raise AssertionError(f"неожиданный вызов модели: {request.task}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if not isinstance(answer.content, request.response_model):
            raise TypeError(
                f"на {request.task} ждали {request.response_model.__name__}, "
                f"в сценарии {type(answer.content).__name__}"
            )
        return answer


# --- сценарий из файла -----------------------------------------------------

# Вид отказа в записи `error` - класс сбоя. `unavailable` - базовый `LlmError`
# с кодом `LLM_UNAVAILABLE`: отдельного подкласса «недоступен» в домене нет.
LLM_SCRIPT_ERRORS: dict[str, type[LlmError]] = {
    "output": LlmOutputError,
    "timeout": LlmTimeoutError,
    "rate_limited": LlmRateLimitedError,
    "unavailable": LlmError,
}
_SCRIPT_MODEL = "script"
_CANDIDATE_ID = re.compile(r"^F[1-9]\d*$")
_ENTRY_KEYS = {
    "review": {"review", "summary", "tokens"},
    "verify": {"verify", "tokens"},
    "error": {"error", "tokens"},
}
_FINDING_KEYS = {"path", "line", "message", "category", "severity", "rule", "side"}


class ScriptError(ValueError):
    """Файл сценария модели не разобран: текст называет запись и что в ней не так."""


def load_llm_script(path: Path) -> ScriptedLlmGateway:
    """Фейк модели над сценарием из JSON-файла: список записей, по одной на вызов.

    Записи (порядок расхода - в docstring модуля):

    - `{"review": [<замечание>, …], "summary"?: {"problem", "positives", "effort"},
      "tokens"?: int}`, замечание - `{"path", "line", "message", "category"?,
      "severity"?, "rule"?: [имя, пункт], "side"?}`;
    - `{"verify": [["F1", "keep" | "drop"], …], "tokens"?: int}`;
    - `{"error": "output" | "timeout" | "rate_limited" | "unavailable", "tokens"?: int}`.

    Лишний ключ, неизвестное значение или неверный тип - `ScriptError` с номером записи.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ScriptError(f"сценарий модели {path} не прочитан: {error}") from error
    except json.JSONDecodeError as error:
        raise ScriptError(f"сценарий модели {path} - не JSON: {error}") from error
    if not isinstance(raw, list):
        raise ScriptError(f"сценарий модели {path}: ждали JSON-список записей")
    return ScriptedLlmGateway([_script_entry(item, i) for i, item in enumerate(raw)])


def _script_entry(item: object, index: int) -> LlmResponse[Any] | Exception:
    where = f"запись {index}"
    if not isinstance(item, dict):
        raise ScriptError(f"{where}: ждали объект JSON")
    kinds = [k for k in _ENTRY_KEYS if k in item]
    if len(kinds) != 1:
        raise ScriptError(f"{where}: нужен ровно один из ключей review, verify, error")
    (kind,) = kinds
    extra = set(item) - _ENTRY_KEYS[kind]
    if extra:
        raise ScriptError(f"{where}: лишние ключи {sorted(extra)}")
    tokens = item.get("tokens", 0)
    if not _is_int(tokens) or tokens < 0:
        raise ScriptError(f"{where}: tokens - целое число не меньше нуля")
    if kind == "review":
        return _script_review(item["review"], item.get("summary"), tokens, where)
    if kind == "verify":
        return _script_verify(item["verify"], tokens, where)
    error_kind = item["error"]
    if not isinstance(error_kind, str) or error_kind not in LLM_SCRIPT_ERRORS:
        raise ScriptError(f"{where}: error - один из {', '.join(LLM_SCRIPT_ERRORS)}")
    return LLM_SCRIPT_ERRORS[error_kind](
        f"сценарий модели: отказ {error_kind}", tokens_used=tokens
    )


def _script_review(
    findings: object, summary: object, tokens: int, where: str
) -> LlmResponse[ReviewOutput]:
    if not isinstance(findings, list):
        raise ScriptError(f"{where}: review - список замечаний")
    payloads = [_script_finding(f, f"{where}, замечание {i}") for i, f in enumerate(findings)]
    try:
        response = review_response(*payloads, tokens=tokens, model=_SCRIPT_MODEL)
    except ValidationError as error:
        raise ScriptError(f"{where}: ответ ревью не прошёл схему: {error}") from error
    if summary is None:
        return response
    if not isinstance(summary, dict):
        raise ScriptError(f"{where}: summary - объект {{problem, positives, effort}}")
    try:
        validated = SummaryOutput.model_validate(summary)
    except ValidationError as error:
        raise ScriptError(f"{where}: summary не прошла схему: {error}") from error
    content = response.content.model_copy(update={"summary": validated})
    return LlmResponse(model=response.model, tokens_used=tokens, content=content)


def _script_finding(item: object, where: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ScriptError(f"{where}: ждали объект JSON")
    extra = set(item) - _FINDING_KEYS
    if extra:
        raise ScriptError(f"{where}: лишние ключи {sorted(extra)}")
    path, line, message = item.get("path"), item.get("line"), item.get("message")
    if not isinstance(path, str) or not path:
        raise ScriptError(f"{where}: path - непустая строка")
    if not _is_int(line) or line < 1:
        raise ScriptError(f"{where}: line - целое число от 1")
    if not isinstance(message, str) or not message:
        raise ScriptError(f"{where}: message - непустая строка")
    rule = item.get("rule")
    rule_ref: tuple[str, int] | None = None
    if rule is not None:
        if not (
            isinstance(rule, list)
            and len(rule) == 2
            and isinstance(rule[0], str)
            and rule[0]
            and _is_int(rule[1])
            and rule[1] >= 1
        ):
            raise ScriptError(f"{where}: rule - пара [имя правила, номер пункта от 1]")
        rule_ref = (rule[0], rule[1])
    return finding_json(
        path,
        line,
        message,
        rule=rule_ref,
        category=_choice(item.get("category", "readability"), FindingCategory, "category", where),
        severity=_choice(item.get("severity", "low"), FindingSeverity, "severity", where),
        side=_choice(item.get("side", "new"), DiffSide, "side", where),
    )


def _script_verify(
    verdicts: object, tokens: int, where: str
) -> LlmResponse[VerificationOutput]:
    if not isinstance(verdicts, list):
        raise ScriptError(f"{where}: verify - список пар [id, вердикт]")
    pairs: list[tuple[str, str]] = []
    for i, pair in enumerate(verdicts):
        if not (isinstance(pair, list) and len(pair) == 2):
            raise ScriptError(f"{where}, вердикт {i}: ждали пару [id, вердикт]")
        vid, verdict = pair
        if not isinstance(vid, str) or not _CANDIDATE_ID.match(vid):
            raise ScriptError(f"{where}, вердикт {i}: id кандидата - F1, F2, …")
        pairs.append((vid, _choice(verdict, VerifierVerdict, "вердикт", f"{where}, вердикт {i}")))
    return verification_response(*pairs, tokens=tokens, model=_SCRIPT_MODEL)


def _choice(
    value: object,
    enum: type[FindingCategory | FindingSeverity | DiffSide | VerifierVerdict],
    name: str,
    where: str,
) -> str:
    allowed = [v.value for v in enum]
    if not isinstance(value, str) or value not in allowed:
        raise ScriptError(f"{where}: {name} - один из {', '.join(allowed)}")
    return value


def _is_int(value: object) -> TypeIs[int]:
    # `bool` - подкласс `int`, но `true` в номере строки - опечатка, а не 1.
    return isinstance(value, int) and not isinstance(value, bool)
