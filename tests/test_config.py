"""Настройки: секреты не просачиваются в текстовые представления, форма валидируется.

`Settings` попадает в `Container` с рабочим `__repr__`; PEM-ключ и секрет
вебхуков — `SecretStr`, а не `str`, поэтому ни repr настроек, ни трейсбек
с локальными переменными их не напечатают. Пустой секрет вебхуков отклоняется
на старте: HMAC с ключом, который знает каждый, — то же самое, что отсутствие
проверки подписи.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings

DATABASE_URL = "postgresql+psycopg://test:test@localhost/test"
RABBITMQ_URL = "amqp://guest:guest@localhost//"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nreal-key-material\n-----END PRIVATE KEY-----"
WEBHOOK_SECRET = "real-webhook-secret-material"
CLIENT_SECRET = "real-client-secret-material"


def make_settings() -> Settings:
    return Settings(
        database_url=DATABASE_URL,
        rabbitmq_url=RABBITMQ_URL,
        github_app_id="123456",
        github_app_private_key=PRIVATE_KEY,
        github_webhook_secret=WEBHOOK_SECRET,
        github_app_client_id="Iv1.client",
        github_app_client_secret=CLIENT_SECRET,
        public_origin="https://review.example/",
    )


def test_repr_hides_both_secrets() -> None:
    rendered = repr(make_settings())
    assert "real-key-material" not in rendered
    assert "real-webhook-secret-material" not in rendered
    assert CLIENT_SECRET not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered


def test_str_hides_both_secrets() -> None:
    rendered = str(make_settings())
    assert "real-key-material" not in rendered
    assert "real-webhook-secret-material" not in rendered
    assert CLIENT_SECRET not in rendered


def test_secrets_are_readable_through_get_secret_value() -> None:
    """Эндпоинту и composition root нужно само значение — через явный вызов."""
    settings = make_settings()
    assert settings.github_app_private_key.get_secret_value() == PRIVATE_KEY
    assert settings.github_webhook_secret.get_secret_value() == WEBHOOK_SECRET


def test_empty_webhook_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=DATABASE_URL, rabbitmq_url=RABBITMQ_URL, github_webhook_secret="")


def test_stale_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url=DATABASE_URL,
            rabbitmq_url=RABBITMQ_URL,
            github_webhook_secret="s",
            stale_run_timeout_seconds=0,
        )


def test_the_client_secret_is_readable_and_the_origin_has_no_trailing_slash() -> None:
    settings = make_settings()
    assert settings.github_app_client_secret.get_secret_value() == CLIENT_SECRET
    assert settings.public_origin == "https://review.example"


def test_context_limits_have_defaults() -> None:
    settings = make_settings()
    assert settings.context_max_tokens == 32_000
    assert settings.pr_context_max_tokens == 1_000
    assert settings.rules_max_tokens < settings.context_max_tokens


def test_llm_limits_have_defaults() -> None:
    settings = make_settings()
    assert settings.llm_context_window_tokens == 65_536
    assert settings.llm_max_output_tokens == 4_096


def test_llm_limits_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "131072")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "8192")
    settings = make_settings()
    assert settings.llm_context_window_tokens == 131_072
    assert settings.llm_max_output_tokens == 8_192


def test_context_limits_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_MAX_TOKENS", "16000")
    monkeypatch.setenv("AST_MAX_FILES", "5")
    settings = make_settings()
    assert settings.context_max_tokens == 16_000
    assert settings.ast_max_files == 5


@pytest.mark.parametrize(
    "field",
    [
        "context_max_tokens",
        "rules_max_tokens",
        "rules_max_bytes",
        "rules_cache_ttl_seconds",
        "ast_max_files",
        "ast_max_file_bytes",
        "llm_context_window_tokens",
        "llm_max_output_tokens",
        "pr_context_max_tokens",
    ],
)
def test_context_limits_must_be_positive(field: str) -> None:
    values: dict[str, Any] = {
        "database_url": DATABASE_URL,
        "rabbitmq_url": RABBITMQ_URL,
        "github_webhook_secret": "s",
        field: 0,
    }
    with pytest.raises(ValidationError):
        Settings(**values)


def _llm_settings(**overrides: Any) -> Settings:
    return Settings(
        database_url=DATABASE_URL,
        rabbitmq_url=RABBITMQ_URL,
        github_webhook_secret=WEBHOOK_SECRET,
        **overrides,
    )


HOSTED = {
    "llm_backend": "ollama",
    "ollama_model": "qwen2.5-coder:7b",
    "llm_hosted_fallback": True,
    "eurorouter_base_url": "https://eurorouter.example/v1",
    "eurorouter_model": "mistral-large-2411",
    "eurorouter_api_keys": "sk-secret-one, sk-secret-two",
}


def test_model_settings_have_defaults() -> None:
    settings = make_settings()
    assert settings.llm_backend is None
    assert settings.ollama_base_url == "http://ollama:11434"
    assert settings.llm_hosted_fallback is False
    assert settings.llm_max_attempts == 2
    assert settings.llm_timeout_seconds == 300


def test_ollama_backend_requires_a_model() -> None:
    with pytest.raises(ValidationError, match="OLLAMA_MODEL"):
        _llm_settings(llm_backend="ollama")


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValidationError, match="llm_backend"):
        _llm_settings(llm_backend="openai")


def test_hosted_fallback_needs_the_local_model_first() -> None:
    with pytest.raises(ValidationError, match="LLM_HOSTED_FALLBACK"):
        _llm_settings(**{**HOSTED, "llm_backend": "stub"})


@pytest.mark.parametrize(
    ("missing", "named"),
    [
        ("eurorouter_base_url", "EUROROUTER_BASE_URL"),
        ("eurorouter_model", "EUROROUTER_MODEL"),
        ("eurorouter_api_keys", "EUROROUTER_API_KEYS"),
    ],
)
def test_hosted_fallback_names_each_missing_parameter(missing: str, named: str) -> None:
    with pytest.raises(ValidationError, match=named):
        _llm_settings(**{**HOSTED, missing: ""})


def test_hosted_keys_are_split_on_commas_and_hidden_from_repr() -> None:
    settings = _llm_settings(**HOSTED)
    assert [key.get_secret_value() for key in settings.eurorouter_keys] == [
        "sk-secret-one",
        "sk-secret-two",
    ]
    assert "sk-secret-one" not in repr(settings)
    assert "sk-secret-one" not in str(settings)


def test_hosted_keys_without_the_flag_are_allowed_but_unused() -> None:
    settings = _llm_settings(**{**HOSTED, "llm_hosted_fallback": False})
    assert settings.llm_hosted_fallback is False


@pytest.mark.parametrize("field", ["llm_max_attempts", "llm_timeout_seconds"])
def test_model_transport_limits_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        _llm_settings(**{field: 0})
