"""Второй проход анализа - общий у ревью и симуляции песочницы."""

from datetime import UTC, datetime
from uuid import UUID

from app.application.llm_review import LlmLimits
from app.application.ports.llm_gateway import LlmTask
from app.application.verify_findings import verify_findings
from app.domain.entities import ContextPayload, DiffAnchor, Finding, Verdict
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    VerificationOutcome,
    VerifierVerdict,
)
from app.domain.llm_errors import LlmTimeoutError
from app.domain.prompt import PromptTexts
from app.domain.untrusted import fence
from app.domain.verification import JudgedFinding

from ..fakes.llm_gateway import ScriptedLlmGateway, verification_response

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
TEXTS = PromptTexts(review_system="РЕВЬЮ", verifier_system="ВЕРИФИКАЦИЯ")
CONTEXT = ContextPayload(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    chunk_index=0,
    tiers=("diff",),
    file_paths=("app/x.py",),
    token_count=10,
    content_sha256="0" * 64,
    body={
        "schema": "context-payload/v1",
        "repository": None,
        "pull_request": None,
        "base_sha": None,
        "head_sha": None,
        "limits": {"max_tokens": 32000},
        "rules": {"status": "absent", "source": ".review/rules.md", "items": [], "rejected": []},
        "diff": [{"path": "app/x.py", "hunk": fence("diff", "@@ -0,0 +1,3 @@\n+a\n+b\n+c")}],
        "ast": [],
        "omitted": [],
        "omitted_count": 0,
    },
    created_at=NOW,
    updated_at=NOW,
)
LIMITS = LlmLimits(window_tokens=65_536, max_output_tokens=4_096)


def _finding(
    n: int,
    category: FindingCategory = FindingCategory.CORRECTNESS,
    severity: FindingSeverity = FindingSeverity.MEDIUM,
) -> Finding:
    return Finding(
        id=UUID(int=100 + n),
        review_run_id=UUID(int=2),
        anchor=DiffAnchor("app/x.py", DiffSide.NEW, new_line=n),
        category=category,
        severity=severity,
        message=f"m{n}",
        created_at=NOW,
        updated_at=NOW,
    )


def _verify(gateway: ScriptedLlmGateway, *candidates: Finding):
    return verify_findings(CONTEXT, TEXTS, gateway, LIMITS, candidates)


def test_without_candidates_the_model_is_not_called() -> None:
    gateway = ScriptedLlmGateway([])

    verification = _verify(gateway)

    assert gateway.requests == []
    assert verification.outcome is VerificationOutcome.NOT_REQUIRED
    assert (verification.kept, verification.dropped, verification.applied) == ((), (), None)
    assert verification.tokens_used == 0


def test_every_kept_finding_carries_its_verdict() -> None:
    first, second = _finding(1), _finding(2)
    serious = _finding(3, FindingCategory.SECURITY, FindingSeverity.CRITICAL)
    gateway = ScriptedLlmGateway(
        [verification_response(("F1", "keep"), ("F2", "drop"), ("F3", "drop"), tokens=7)]
    )

    verification = _verify(gateway, first, second, serious)

    (request,) = gateway.requests
    assert request.task is LlmTask.VERIFY
    assert verification.outcome is VerificationOutcome.VERIFIED
    assert verification.kept == (first, serious)
    assert verification.applied is not None
    assert verification.applied.kept == (
        JudgedFinding(first, Verdict(VerifierVerdict.KEEP, "причина F1")),
        JudgedFinding(serious, Verdict(VerifierVerdict.DROP, "причина F3")),
    )
    assert verification.dropped == (JudgedFinding(second, Verdict(VerifierVerdict.DROP, "причина F2")),)
    assert (verification.tokens_used, verification.step_tokens, verification.error_code) == (7, 7, None)


def test_model_failure_keeps_the_candidates_without_verdicts() -> None:
    candidates = (_finding(1), _finding(2))
    gateway = ScriptedLlmGateway([LlmTimeoutError("таймаут", tokens_used=3)])

    verification = _verify(gateway, *candidates)

    assert verification.outcome is VerificationOutcome.UNAVAILABLE
    assert (verification.kept, verification.dropped, verification.applied) == (candidates, (), None)
    assert (verification.tokens_used, verification.step_tokens) == (3, 3)
    assert verification.error_code == "LLM_TIMEOUT"


def test_answer_without_a_verdict_for_every_candidate_keeps_them_all() -> None:
    candidates = (_finding(1), _finding(2))
    gateway = ScriptedLlmGateway([verification_response(("F1", "drop"), tokens=5)])

    verification = _verify(gateway, *candidates)

    assert verification.outcome is VerificationOutcome.UNAVAILABLE
    assert (verification.kept, verification.applied) == (candidates, None)
    assert (verification.tokens_used, verification.error_code) == (5, "LLM_INVALID_OUTPUT")
