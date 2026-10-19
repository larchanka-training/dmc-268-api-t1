"""Живые тесты против настоящего EUrouter (change `add-llm-gateway`, design D11).

Платный вызов, поэтому маркер `live`: основной прогон их исключает, а запускает
workflow `.github/workflows/llm-live.yml` с ключом организации. Без
`EUROROUTER_API_KEYS` в окружении тесты пропускаются.

Запросы только синтетические: код проверяемых репозиториев наружу не уходит.
Проверяется то, что нельзя проверить на записанных ответах: что настоящий
провайдер принимает наш протокол (`json_schema`, `max_tokens`), отвергает
неверный ключ так, как ждёт ротация, и что контейнер из окружения доходит до
него фоллбэком.
"""

import logging
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from app.application.llm_review import request_review
from app.application.llm_schema import ReviewOutput, VerificationOutput
from app.application.ports.llm_gateway import LlmRequest, LlmTask
from app.config import Settings
from app.domain.entities import ContextPayload, PromptMessage
from app.domain.enums import PromptRole
from app.domain.untrusted import fence
from app.infrastructure.container import build_container
from app.infrastructure.llm.openai_compatible import OpenAiCompatibleGateway
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.worker.factory import llm_limits, load_checked_prompt_texts

from ..conftest import requires_eurorouter

pytestmark = [pytest.mark.live, requires_eurorouter]

HUNK = (
    "@@ -10,2 +10,3 @@ def find_user(cursor, name):\n"
    '-    cursor.execute("SELECT id FROM users WHERE name = %s", (name,))\n'
    "+    query = f\"SELECT id FROM users WHERE name = '{name}'\"\n"
    "+    cursor.execute(query)\n"
    "     return cursor.fetchone()"
)
SHORT_REVIEW = (
    PromptMessage(PromptRole.SYSTEM, "Ты ревьюер кода. Ответь одним JSON по схеме; замечаний может не быть."),
    PromptMessage(PromptRole.USER, f'Файл "app/users.py":\n{HUNK}'),
)
SHORT_VERIFICATION = (
    PromptMessage(
        PromptRole.SYSTEM,
        "Ты верификатор замечаний ревью. По каждому замечанию F1, F2, … верни вердикт keep или drop "
        "с причиной одним предложением. Ответь одним JSON по схеме.",
    ),
    PromptMessage(
        PromptRole.USER,
        f'Файл "app/users.py":\n{HUNK}\n\n### F1\n'
        '{"filePath": "app/users.py", "side": "new", "line": 11, "category": "security"}\n'
        "Текст: запрос собирается f-строкой из name - SQL-инъекция.",
    ),
)


def _settings() -> Settings:
    """Настройки из окружения workflow; локальная модель нарочно недоступна.

    Порт 9 (discard) на loopback закрыт: `OllamaGateway` получает отказ в
    соединении сразу, и фоллбэк уходит на EUrouter без ожидания таймаута.
    """
    return Settings(
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="live-test",
        llm_backend="ollama",
        ollama_model="unused-local-model",
        ollama_base_url="http://127.0.0.1:9",
        llm_hosted_fallback=True,
    )


def _eurorouter(settings: Settings, keys: tuple[SecretStr, ...]) -> OpenAiCompatibleGateway:
    client = httpx.Client(
        base_url=settings.eurorouter_base_url, timeout=httpx.Timeout(settings.llm_timeout_seconds)
    )
    return OpenAiCompatibleGateway(
        client,
        settings.eurorouter_model,
        keys,
        name="Eurorouter",
        max_attempts=settings.llm_max_attempts,
    )


@pytest.mark.parametrize(
    ("task", "messages", "response_model"),
    [
        (LlmTask.REVIEW, SHORT_REVIEW, ReviewOutput),
        (LlmTask.VERIFY, SHORT_VERIFICATION, VerificationOutput),
    ],
    ids=["review", "verification"],
)
def test_eurorouter_answers_by_the_json_schema_of_the_request(
    task: LlmTask, messages: tuple[PromptMessage, ...], response_model: type[BaseModel]
) -> None:
    settings = _settings()
    request = LlmRequest(task, messages, response_model, max_output_tokens=1_024)

    response = _eurorouter(settings, settings.eurorouter_keys).complete(request)

    assert isinstance(response.content, response_model)
    assert response.model
    assert response.tokens_used > 0


def test_rejected_key_is_replaced_by_the_next_one(caplog: pytest.LogCaptureFixture) -> None:
    settings = _settings()
    keys = (SecretStr("sk-live-test-invalid-key"), *settings.eurorouter_keys)
    request = LlmRequest(LlmTask.REVIEW, SHORT_REVIEW, ReviewOutput, max_output_tokens=1_024)

    with caplog.at_level(logging.WARNING, logger="app.infrastructure.llm.openai_compatible"):
        response = _eurorouter(settings, keys).complete(request)

    assert isinstance(response.content, ReviewOutput)
    log = "\n".join(record.getMessage() for record in caplog.records)
    assert re.search(rf"ключ 1 из {len(keys)} отвергнут \(HTTP 40[13]\)", log), log


def test_container_from_the_environment_falls_back_to_eurorouter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = build_container(_settings()).llm_gateway()
    request = LlmRequest(LlmTask.REVIEW, SHORT_REVIEW, ReviewOutput, max_output_tokens=1_024)

    with caplog.at_level(logging.WARNING, logger="app.infrastructure.llm.fallback"):
        response = gateway.complete(request)

    assert isinstance(response.content, ReviewOutput)
    log = "\n".join(record.getMessage() for record in caplog.records)
    assert "модель Ollama не ответила на review" in log
    assert "запрос уходит Eurorouter" in log


def test_full_review_request_with_the_shipped_prompts_is_answered_by_the_schema() -> None:
    settings = _settings()
    now = datetime.now(UTC)
    body = {
        "schema": "context-payload/v1",
        "repository": "owner/repo",
        "pull_request": 7,
        "base_sha": "b" * 40,
        "head_sha": "a" * 40,
        "limits": {"max_tokens": settings.context_max_tokens},
        "pull_request_context": [
            {"part": "title", "text": fence("pr_title", "refactor: упростить поиск пользователя")}
        ],
        "rules": {"status": "absent", "source": ".review/rules.md", "items": [], "rejected": []},
        "diff": [{"path": "app/users.py", "hunk": fence("diff", HUNK)}],
        "ast": [],
        "omitted": [],
        "omitted_count": 0,
    }
    context = ContextPayload(
        id=UUID(int=1),
        review_run_id=UUID(int=2),
        chunk_index=0,
        tiers=("diff",),
        file_paths=("app/users.py",),
        token_count=200,
        content_sha256="0" * 64,
        body=body,
        created_at=now,
        updated_at=now,
    )
    texts = load_checked_prompt_texts(settings, DEFAULT_PROMPTS_DIR)

    result = request_review(
        context, texts, build_container(settings).llm_gateway(), limits=llm_limits(settings)
    )

    assert result.model
    assert result.tokens_used > 0
    assert result.summary is not None
