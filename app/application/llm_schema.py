"""Форма ответов модели - Pydantic-модели, единственный её источник.

Модели отдаются адаптеру в `LlmRequest.response_model`: из них он берёт JSON
Schema для провайдера и ими же проверяет ответ. Документация
(`docs/openapi/llm_review_output.schema.json`,
`llm_verification_output.schema.json`) с ними сверена тестом, поэтому правило
валидации записано один раз и не повторяется ни в адаптерах, ни здесь.

Строгость - точечная: числа и булевы значения не приводятся из строк (`"3"`
не станет строкой 3), а строки и перечисления и без того не принимают
чужого. Лишнее поле - ошибка: модель, дописавшая своё, ответила не по форме.

Pydantic живёт только здесь и в порте; остальной код получает dataclass'ы
`LlmReviewResult` и доменные `Verdict` из чистых функций ниже.
"""

from collections import Counter
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.application.ports.llm_gateway import (
    LlmFinding,
    LlmReviewResult,
    LlmReviewSummary,
    LlmRuleRef,
    LlmThreadFollowUp,
)
from app.domain.entities import DiffAnchor, Verdict
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    ThreadFollowUpStatus,
    VerifierVerdict,
)
from app.domain.result import Result

# Обычные присваивания, а не `type X = …`: псевдоним PEP 695 Pydantic выносит
# в `$defs` отдельным определением, и схема для провайдера обрастает ссылками.
_Text = Annotated[str, Field(min_length=1)]
_Ordinal = Annotated[int, Field(strict=True, ge=1)]
_Confidence = Annotated[float, Field(strict=True, ge=0, le=1)]


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, alias_generator=to_camel)


class SuggestionOutput(_Output):
    diff: str


class RuleRefOutput(_Output):
    name: _Text
    check: _Ordinal


class FindingOutput(_Output):
    file_path: _Text
    side: DiffSide
    line: _Ordinal
    category: FindingCategory
    severity: FindingSeverity
    message: _Text
    confidence: _Confidence | None
    suggestion: SuggestionOutput | None = None
    rule: RuleRefOutput | None = None


class SummaryOutput(_Output):
    problem: _Text
    positives: tuple[str, ...]
    effort: _Text


class ThreadAnchorOutput(_Output):
    file_path: _Text
    side: DiffSide
    line: _Ordinal
    category: FindingCategory


class ThreadFollowUpOutput(_Output):
    anchor: ThreadAnchorOutput
    status: ThreadFollowUpStatus
    reply: _Text
    resolve_thread: Annotated[bool, Field(strict=True)] = False


class ReviewOutput(_Output):
    """Ответ модели на запрос ревью."""

    schema_version: Literal["1"]
    findings: tuple[FindingOutput, ...]
    summary: SummaryOutput
    thread_follow_ups: tuple[ThreadFollowUpOutput, ...] = ()


class VerdictOutput(_Output):
    id: _Text
    verdict: VerifierVerdict
    reason: _Text


class VerificationOutput(_Output):
    """Ответ верификатора: по вердикту на каждое переданное замечание."""

    schema_version: Literal["1"]
    verdicts: tuple[VerdictOutput, ...]


def _anchor(file_path: str, side: DiffSide, line: int) -> DiffAnchor:
    if side is DiffSide.NEW:
        return DiffAnchor(file_path=file_path, side=side, new_line=line)
    return DiffAnchor(file_path=file_path, side=side, old_line=line)


def to_review_result(output: ReviewOutput, *, model: str, tokens_used: int) -> LlmReviewResult:
    """Перевести проверенный ответ ревью в структуры приложения."""
    return LlmReviewResult(
        model=model,
        tokens_used=tokens_used,
        findings=tuple(
            LlmFinding(
                anchor=_anchor(f.file_path, f.side, f.line),
                category=f.category,
                severity=f.severity,
                message=f.message,
                suggestion=f.suggestion.diff if f.suggestion is not None else None,
                confidence=f.confidence,
                rule=LlmRuleRef(name=f.rule.name, check=f.rule.check) if f.rule is not None else None,
            )
            for f in output.findings
        ),
        summary=LlmReviewSummary(
            problem=output.summary.problem,
            positives=output.summary.positives,
            effort=output.summary.effort,
        ),
        thread_follow_ups=tuple(
            LlmThreadFollowUp(
                anchor=_anchor(t.anchor.file_path, t.anchor.side, t.anchor.line),
                category=t.anchor.category,
                status=t.status,
                reply=t.reply,
                resolve_thread=t.resolve_thread,
            )
            for t in output.thread_follow_ups
        ),
    )


def check_verdicts(output: VerificationOutput, ids: Sequence[str]) -> Result[dict[str, Verdict]]:
    """Ровно один вердикт на каждый переданный идентификатор и ни одного лишнего.

    Схема этого не выражает, а без проверки верификатор мог бы молча
    пропустить замечание или «вынести вердикт» замечанию, которого не было.
    """
    counts = Counter(v.id for v in output.verdicts)
    repeated = sorted(i for i, n in counts.items() if n > 1)
    unknown = sorted(set(counts) - set(ids))
    missing = [i for i in ids if i not in counts]
    problems = [
        f"{label}: {', '.join(found)}"
        for label, found in (("повторы", repeated), ("лишние", unknown), ("без вердикта", missing))
        if found
    ]
    if problems:
        return Result.failure("ответ верификатора не покрывает замечания: " + "; ".join(problems))
    return Result.success({v.id: Verdict(v.verdict, v.reason) for v in output.verdicts})
