import pytest
from pydantic import BaseModel

from app.application.llm_schema import ReviewOutput, VerificationOutput
from app.application.ports.llm_gateway import LlmRequest, LlmTask
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.infrastructure.llm.stub import StubLlmGateway

MESSAGES = (PromptMessage(PromptRole.SYSTEM, "инструкции"), PromptMessage(PromptRole.USER, "код"))


@pytest.mark.parametrize(
    ("task", "model"),
    [(LlmTask.REVIEW, ReviewOutput), (LlmTask.VERIFY, VerificationOutput)],
)
def test_stub_answers_with_an_empty_instance_of_the_requested_model(
    task: LlmTask, model: type[BaseModel]
) -> None:
    response = StubLlmGateway().complete(LlmRequest(task, MESSAGES, model))

    assert response.model == "stub-llm"
    assert response.tokens_used == 0
    assert isinstance(response.content, model)


def test_stub_review_has_no_findings() -> None:
    response = StubLlmGateway().complete(LlmRequest(LlmTask.REVIEW, MESSAGES, ReviewOutput))

    assert response.content.findings == ()
    assert response.content.thread_follow_ups == ()


def test_stub_verification_has_no_verdicts() -> None:
    response = StubLlmGateway().complete(LlmRequest(LlmTask.VERIFY, MESSAGES, VerificationOutput))

    assert response.content.verdicts == ()
