"""Поддельный `LlmGateway`: заранее заданные ответы по одному на вызов и запись запросов.

Ответы задаются в форме провода (JSON моделей ответа), чтобы тест проходил
через ту же проверку формы, что и ответ настоящего провайдера.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.application.llm_schema import ReviewOutput, VerificationOutput
from app.application.ports.llm_gateway import LlmRequest, LlmResponse

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
