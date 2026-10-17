"""Фасад «пакет контекста → ответ модели в структурах приложения».

Связывает три части, ни одна из которых не знает о других: сборку запроса
(`app/domain/prompt.py`), порт к модели и форму ответа
(`app/application/llm_schema.py`). Пайплайн вызывает только его, адаптеры
провайдеров к нему не прикасаются.

Сбой порта (`LlmError`, `LlmOutputError`) пробрасывается: решение, что с ним
делать, - за пайплайном (ревью - сбой прогона, верификация - fail-open).
Неполные вердикты верификатора - не исключение, а неудачный `Result`: ответ
пришёл и израсходовал токены, их надо учесть.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.application.llm_schema import (
    ReviewOutput,
    VerificationOutput,
    check_verdicts,
    to_review_result,
)
from app.application.ports.llm_gateway import (
    LlmGateway,
    LlmRequest,
    LlmReviewResult,
    LlmTask,
)
from app.domain.entities import ContextPayload, Finding, Verdict
from app.domain.prompt import (
    PromptTexts,
    build_review_prompt,
    build_verification_prompt,
    candidate_ids,
)
from app.domain.result import Result


@dataclass(frozen=True, slots=True)
class LlmLimits:
    """Окно модели и резерв в нём под ответ - из конфигурации, как `ContextLimits`."""

    window_tokens: int
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class VerificationAnswer:
    verdicts: Result[dict[str, Verdict]]
    tokens_used: int


def request_review(
    context: ContextPayload, texts: PromptTexts, gateway: LlmGateway, *, limits: LlmLimits
) -> LlmReviewResult:
    request = LlmRequest(
        LlmTask.REVIEW,
        build_review_prompt(texts, context.body),
        ReviewOutput,
        max_output_tokens=limits.max_output_tokens,
    )
    response = gateway.complete(request)
    return to_review_result(response.content, model=response.model, tokens_used=response.tokens_used)


def request_verification(
    context: ContextPayload,
    texts: PromptTexts,
    gateway: LlmGateway,
    candidates: Sequence[Finding],
    *,
    limits: LlmLimits,
) -> VerificationAnswer:
    request = LlmRequest(
        LlmTask.VERIFY,
        build_verification_prompt(texts, context.body, candidates),
        VerificationOutput,
        max_output_tokens=limits.max_output_tokens,
    )
    response = gateway.complete(request)
    return VerificationAnswer(
        verdicts=check_verdicts(response.content, candidate_ids(len(candidates))),
        tokens_used=response.tokens_used,
    )
