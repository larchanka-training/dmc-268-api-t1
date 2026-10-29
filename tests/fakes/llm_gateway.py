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

Формат записи - в `load_llm_script`: внутри конверта сценария - те же модели
провода `app/application/llm_schema.py`, что проверяют ответ настоящей модели,
поэтому своей проверки полей у сценария нет.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.application.llm_schema import ReviewOutput, VerificationOutput
from app.application.ports.llm_gateway import LlmRequest, LlmResponse
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
# Ключ вида записи и прочие ключи, кроме `tokens`, допустимые рядом с ним.
_ENTRY_KEYS = {"review": {"summary"}, "verify": set(), "error": set()}
# Чего нет в замечании или вердикте сценария, берётся отсюда; остальное
# обязательно по схеме провода.
_FINDING_DEFAULTS = {"side": "new", "category": "readability", "severity": "low", "confidence": None}
_VERDICT_DEFAULTS = {"reason": "сценарий"}


class ScriptError(ValueError):
    """Файл сценария модели не разобран: текст называет запись и что в ней не так."""


def load_llm_script(path: Path) -> ScriptedLlmGateway:
    """Фейк модели над сценарием из JSON-файла: список записей, по одной на вызов.

    Запись - ровно один из ключей `review`, `verify`, `error` и необязательный
    `tokens` (целое от 0, по умолчанию 0):

    - `{"review": [<FindingOutput>, …], "summary"?: <SummaryOutput>}` - замечания
      в форме провода (`filePath`, `line`, `message`, `suggestion`: `{"diff"}`,
      `rule`: `{"name", "check"}`, …); не указанные `side`, `category`,
      `severity`, `confidence` - `new`, `readability`, `low`, `null`, сводка -
      `SUMMARY`. Либо `{"review": <ReviewOutput>}` - ответ модели целиком,
      со `schemaVersion` и `summary`, как его вернула модель;
    - `{"verify": [<VerdictOutput>, …]}` - `{"id": "F1", "verdict": "keep" | "drop",
      "reason"?}`, либо `{"verify": <VerificationOutput>}` целиком;
    - `{"error": "output" | "timeout" | "rate_limited" | "unavailable"}`.

    Ответ проверяют модели `app/application/llm_schema.py`: лишний ключ, чужое
    значение или неверный тип - `ScriptError` с номером записи и местом в ней.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ScriptError(f"сценарий модели {path} не прочитан: {error}") from error
    except json.JSONDecodeError as error:
        raise ScriptError(f"сценарий модели {path} - не JSON: {error}") from error
    if not isinstance(raw, list):
        raise ScriptError(f"сценарий модели {path}: ждали JSON-список записей")
    return ScriptedLlmGateway([_script_entry(item, f"запись {i}") for i, item in enumerate(raw)])


def _script_entry(item: object, where: str) -> LlmResponse[Any] | Exception:
    if not isinstance(item, dict):
        raise ScriptError(f"{where}: ждали объект JSON")
    kinds = [k for k in _ENTRY_KEYS if k in item]
    if len(kinds) != 1:
        raise ScriptError(f"{where}: нужен ровно один из ключей {', '.join(_ENTRY_KEYS)}")
    (kind,) = kinds
    extra = set(item) - {kind, "tokens", *_ENTRY_KEYS[kind]}
    if extra:
        raise ScriptError(f"{where}: лишние ключи {sorted(extra)}")
    tokens = item.get("tokens", 0)
    # `type is int`, а не `isinstance`: `true` в токенах - опечатка, а не 1.
    if type(tokens) is not int or tokens < 0:
        raise ScriptError(f"{where}: tokens - целое число не меньше нуля")
    if kind == "error":
        error_kind = item["error"]
        if not isinstance(error_kind, str) or error_kind not in LLM_SCRIPT_ERRORS:
            raise ScriptError(f"{where}: error - один из {', '.join(LLM_SCRIPT_ERRORS)}")
        return LLM_SCRIPT_ERRORS[error_kind](f"сценарий модели: отказ {error_kind}", tokens_used=tokens)
    try:
        content = _review(item, where) if kind == "review" else _verification(item["verify"])
    except ValidationError as error:
        raise ScriptError(f"{where}: {kind} не прошёл схему ответа модели: {error}") from error
    return LlmResponse(model=_SCRIPT_MODEL, tokens_used=tokens, content=content)


def _review(item: dict[str, Any], where: str) -> ReviewOutput:
    findings = item["review"]
    if isinstance(findings, dict):
        if "summary" in item:
            raise ScriptError(f"{where}: у ответа модели целиком summary - внутри review")
        return ReviewOutput.model_validate(findings)
    return ReviewOutput.model_validate(
        {
            "schemaVersion": "1",
            "findings": _with_defaults(findings, _FINDING_DEFAULTS),
            "summary": item.get("summary", SUMMARY),
        }
    )


def _verification(verdicts: object) -> VerificationOutput:
    if isinstance(verdicts, dict):
        return VerificationOutput.model_validate(verdicts)
    return VerificationOutput.model_validate(
        {"schemaVersion": "1", "verdicts": _with_defaults(verdicts, _VERDICT_DEFAULTS)}
    )


def _with_defaults(items: object, defaults: dict[str, Any]) -> object:
    """Дописать умолчания в объекты списка; что не список и не объект - схема отвергнет сама."""
    if not isinstance(items, list):
        return items
    return [defaults | item if isinstance(item, dict) else item for item in items]
