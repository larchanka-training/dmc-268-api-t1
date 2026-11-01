"""Второй проход анализа - общий у ревью запроса на изменение и симуляции песочницы.

Верификатор может только отбросить замечание (спека `review-verification`),
а его сбой анализ не роняет: fail-open, кандидаты остаются как есть, итог -
`unavailable`. Что делать с отброшенными и оставленными вопреки `drop` -
писать в лог прогона или показывать пользователю, - решает вызывающий: в
причинах верификатора бывает код, а код из песочницы в лог не пишется.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.application.llm_review import LlmLimits, request_verification
from app.application.ports.llm_gateway import LlmGateway, RetryListener
from app.application.review_run_steps import spent_tokens
from app.domain.entities import ContextPayload, Finding
from app.domain.enums import VerificationOutcome
from app.domain.llm_errors import LlmError, LlmOutputError
from app.domain.prompt import PromptTexts
from app.domain.verification import AppliedVerdicts, JudgedFinding, apply_verdicts

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Verification:
    candidates: tuple[Finding, ...]
    outcome: VerificationOutcome
    tokens_used: int
    # Вердикты - ровно у `verified`: без кандидатов модель не вызывалась, при
    # fail-open вердиктов нет.
    applied: AppliedVerdicts | None = None
    # Для шага `verifying`: токены, если модель вызывалась, и код сбоя, если
    # второй проход не удался (анализ при этом не падает).
    step_tokens: int | None = None
    error_code: str | None = None

    @property
    def kept(self) -> tuple[Finding, ...]:
        """Что сохранить: без вердиктов - все кандидаты."""
        if self.applied is None:
            return self.candidates
        return tuple(judged.finding for judged in self.applied.kept)

    @property
    def dropped(self) -> tuple[JudgedFinding, ...]:
        return self.applied.dropped if self.applied is not None else ()


def verify_findings(
    context: ContextPayload,
    texts: PromptTexts,
    gateway: LlmGateway,
    limits: LlmLimits,
    candidates: Sequence[Finding],
    *,
    on_retry: RetryListener | None = None,
) -> Verification:
    """Оставить замечания, которые верификатор подтвердил, и те, что отбросить нельзя.

    Без кандидатов модель не вызывается. Сбой вызова или ответ, не покрывающий
    кандидатов ровно по разу, - fail-open: кандидаты остаются как есть, а
    итог `unavailable`. Прочие исключения не гасятся.
    """
    kept = tuple(candidates)
    if not kept:
        return Verification(kept, VerificationOutcome.NOT_REQUIRED, 0)
    try:
        answer = request_verification(
            context, texts, gateway, kept, limits=limits, on_retry=on_retry
        )
    except LlmError as error:
        # Идентификатор прогона или симуляции - атрибутом из контекста логов, не в тексте.
        logger.warning("верификатор недоступен, остаются замечания после отсева", exc_info=True)
        return Verification(
            kept,
            VerificationOutcome.UNAVAILABLE,
            error.tokens_used,
            step_tokens=spent_tokens(error),
            error_code=error.code,
        )
    if not answer.verdicts.ok:
        logger.warning("ответ верификатора не принят: %s", answer.verdicts.error)
        return Verification(
            kept,
            VerificationOutcome.UNAVAILABLE,
            answer.tokens_used,
            step_tokens=answer.tokens_used,
            error_code=LlmOutputError.code,
        )
    return Verification(
        kept,
        VerificationOutcome.VERIFIED,
        answer.tokens_used,
        applied=apply_verdicts(kept, answer.verdicts.unwrap()),
        step_tokens=answer.tokens_used,
    )
