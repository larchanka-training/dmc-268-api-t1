"""Фасад «пакет контекста → ответ модели в структурах приложения»."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.llm_review import LlmLimits, request_review, request_verification
from app.application.llm_schema import ReviewOutput, VerificationOutput
from app.application.ports.llm_gateway import LlmRuleRef, LlmTask
from app.domain.entities import ContextPayload, DiffAnchor, Finding, Verdict
from app.domain.enums import DiffSide, FindingCategory, FindingSeverity, VerifierVerdict
from app.domain.llm_errors import LlmOutputError, PromptTooLargeError
from app.domain.prompt import (
    PromptTexts,
    build_review_prompt,
    build_verification_prompt,
)
from app.domain.untrusted import fence

from ..fakes.llm_gateway import (
    ScriptedLlmGateway,
    finding_json,
    review_response,
    verification_response,
)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
TEXTS = PromptTexts(review_system="РЕВЬЮ", verifier_system="ВЕРИФИКАЦИЯ")
BODY = {
    "schema": "context-payload/v1",
    "repository": "owner/repo",
    "pull_request": 42,
    "base_sha": "b" * 40,
    "head_sha": "a" * 40,
    "limits": {"max_tokens": 32000},
    "rules": {"status": "absent", "source": ".review/rules.md", "items": [], "rejected": []},
    "diff": [{"path": "app/x.py", "hunk": fence("diff", "@@ -0,0 +1 @@\n+x = 1")}],
    "ast": [],
    "omitted": [],
    "omitted_count": 0,
}
CONTEXT = ContextPayload(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    chunk_index=0,
    tiers=("diff",),
    file_paths=("app/x.py",),
    token_count=10,
    content_sha256="0" * 64,
    body=BODY,
    created_at=NOW,
    updated_at=NOW,
)
LIMITS = LlmLimits(window_tokens=65_536, max_output_tokens=4_096)
CANDIDATE = Finding(
    id=UUID(int=3),
    review_run_id=UUID(int=2),
    anchor=DiffAnchor("app/x.py", DiffSide.NEW, new_line=1),
    category=FindingCategory.CORRECTNESS,
    severity=FindingSeverity.HIGH,
    message="m",
    created_at=NOW,
    updated_at=NOW,
)


def test_review_sends_the_assembled_prompt_and_the_review_model() -> None:
    gateway = ScriptedLlmGateway(
        [review_response(finding_json("app/x.py", 1, "m", rule=("R", 2)), tokens=9, model="qwen")]
    )

    result = request_review(CONTEXT, TEXTS, gateway, limits=LIMITS)

    (request,) = gateway.requests
    assert request.task is LlmTask.REVIEW
    assert request.messages == build_review_prompt(TEXTS, BODY)
    assert request.response_model is ReviewOutput
    assert request.max_output_tokens == 4_096
    assert (result.model, result.tokens_used) == ("qwen", 9)
    assert result.findings[0].rule == LlmRuleRef("R", 2)


def test_review_output_error_propagates() -> None:
    gateway = ScriptedLlmGateway([LlmOutputError("findings.0.severity: Field required")])
    with pytest.raises(LlmOutputError, match="severity"):
        request_review(CONTEXT, TEXTS, gateway, limits=LIMITS)


def test_verification_sends_the_candidates_and_checks_the_verdicts() -> None:
    gateway = ScriptedLlmGateway([verification_response(("F1", "drop"), tokens=4)])

    answer = request_verification(CONTEXT, TEXTS, gateway, (CANDIDATE,), limits=LIMITS)

    (request,) = gateway.requests
    assert request.task is LlmTask.VERIFY
    assert request.messages == build_verification_prompt(TEXTS, BODY, (CANDIDATE,))
    assert request.response_model is VerificationOutput
    assert request.max_output_tokens == 4_096
    assert answer.tokens_used == 4
    assert answer.verdicts.unwrap() == {"F1": Verdict(VerifierVerdict.DROP, "причина F1")}


def test_verification_that_misses_a_candidate_is_a_failed_result_not_an_exception() -> None:
    gateway = ScriptedLlmGateway([verification_response(tokens=4)])

    answer = request_verification(CONTEXT, TEXTS, gateway, (CANDIDATE,), limits=LIMITS)

    assert not answer.verdicts.ok
    assert "F1" in (answer.verdicts.error or "")
    assert answer.tokens_used == 4


def test_review_over_the_model_window_is_never_sent() -> None:
    gateway = ScriptedLlmGateway([])
    tight = LlmLimits(window_tokens=100, max_output_tokens=50)

    with pytest.raises(PromptTooLargeError, match="больше окна 100"):
        request_review(CONTEXT, TEXTS, gateway, limits=tight)

    assert gateway.requests == []


def test_verification_over_the_model_window_is_never_sent() -> None:
    gateway = ScriptedLlmGateway([])
    tight = LlmLimits(window_tokens=100, max_output_tokens=50)

    with pytest.raises(PromptTooLargeError, match="больше окна 100"):
        request_verification(CONTEXT, TEXTS, gateway, (CANDIDATE,), limits=tight)

    assert gateway.requests == []
