"""Модель-оракул: отвечает ровно размеченными нарушениями кейса.

Нужна, чтобы обвязка eval проверялась на каждом PR без платной модели:
прогон корпуса с оракулом обязан дать Precision и Recall, равные единице.
Если нет - сломана разметка или стенд (дифф, привязка, отсев, публикация), а
не модель.

Кейс узнаётся по пути файла в сообщении с кодом. На пункт правила оракул
ссылается, только если правило показано в сообщении с правилами, - как честная
модель: без `rules.md` ей не на что сослаться.
"""

import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.application.ports.llm_gateway import LlmRequest, LlmResponse, LlmTask

from .corpus import Case

_SUMMARY = {"problem": "Есть замечания.", "positives": [], "effort": "час"}
_CANDIDATE = re.compile(r"^### (F\d+)$", re.MULTILINE)


class OracleLlmGateway:
    def __init__(self, cases: Sequence[Case]) -> None:
        self._cases = cases

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        rules, code = request.messages[1].content, request.messages[2].content
        if request.task is LlmTask.REVIEW:
            body: dict[str, Any] = {
                "schemaVersion": "1",
                "findings": self._findings(rules, code),
                "summary": _SUMMARY,
            }
        else:
            candidates = _CANDIDATE.findall(request.messages[3].content)
            body = {
                "schemaVersion": "1",
                "verdicts": [
                    {"id": c, "verdict": "keep", "reason": "по разметке"} for c in candidates
                ],
            }
        return LlmResponse(
            model="oracle", tokens_used=0, content=request.response_model.model_validate(body)
        )

    def _findings(self, rules: str, code: str) -> list[dict[str, Any]]:
        (case,) = [c for c in self._cases if any(json.dumps(p) in code for p in c.files)]
        return [
            {
                "filePath": issue.path,
                "side": "new",
                "line": issue.line,
                "category": issue.category or "readability",
                "severity": "medium",
                "message": f"Нарушение {issue.id}.",
                "confidence": None,
                "rule": (
                    {"name": issue.rule[0], "check": issue.rule[1]}
                    if issue.rule and f"### Правило {json.dumps(issue.rule[0])}" in rules
                    else None
                ),
            }
            for issue in case.expected
        ]
