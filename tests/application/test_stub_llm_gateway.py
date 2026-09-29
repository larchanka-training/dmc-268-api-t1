from datetime import UTC, datetime
from uuid import UUID

from app.domain.entities import ContextPayload
from app.infrastructure.llm.stub import StubLlmGateway

CONTEXT = ContextPayload(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    chunk_index=0,
    tiers=("diff",),
    file_paths=("PLACEHOLDER.md",),
    token_count=0,
    content_sha256="0" * 64,
    body={},
    created_at=datetime(2026, 9, 29, tzinfo=UTC),
    updated_at=datetime(2026, 9, 29, tzinfo=UTC),
)


def test_stub_returns_a_fixed_result_with_no_findings() -> None:
    result = StubLlmGateway().review(CONTEXT)
    assert result.model == "stub-llm"
    assert result.tokens_used == 0
    assert result.findings == ()
