"""Composition root связывает каждый порт ровно один раз — и только он."""

import base64
import inspect
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.api.dependencies import get_unit_of_work
from app.application import ports
from app.config import Settings
from app.infrastructure.container import (
    WORKER_VCS_LIMITS,
    build_container,
    llm_http_timeout,
)
from app.infrastructure.llm.fallback import FallbackLlmGateway
from app.infrastructure.llm.stub import StubLlmGateway

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)


@dataclass(frozen=True)
class Adapter:
    """Где живёт адаптер порта: файл внутри infrastructure и имя класса."""

    source: str
    name: str


PORT_TO_ADAPTER: dict[str, Adapter] = {
    "CacheStore": Adapter("cache/memory.py", "InMemoryCacheStore"),
    "DefinitionExtractor": Adapter(
        "parsing/tree_sitter_extractor.py", "TreeSitterDefinitionExtractor"
    ),
    "ContextPayloadRepo": Adapter(
        "db/repositories.py", "SqlAlchemyContextPayloadRepo"
    ),
    "FindingRepo": Adapter("db/repositories.py", "SqlAlchemyFindingRepo"),
    "InstallationCatalog": Adapter("vcs/github_installations.py", "GitHubInstallationCatalog"),
    "JobQueue": Adapter("queue/rabbitmq.py", "RabbitMQJobQueue"),
    "LlmGateway": Adapter("llm/stub.py", "StubLlmGateway"),
    "MergeRequestRepo": Adapter("db/repositories.py", "SqlAlchemyMergeRequestRepo"),
    "OAuthProvider": Adapter("oauth/github.py", "GitHubOAuthProvider"),
    "ProviderTokenRepo": Adapter("db/repositories.py", "SqlAlchemyProviderTokenRepo"),
    "PublishedCommentRepo": Adapter(
        "db/repositories.py", "SqlAlchemyPublishedCommentRepo"
    ),
    "RepositoryRepo": Adapter("db/repositories.py", "SqlAlchemyRepositoryRepo"),
    "ReviewRunRepo": Adapter("db/repositories.py", "SqlAlchemyReviewRunRepo"),
    "ReviewRunStepRepo": Adapter("db/repositories.py", "SqlAlchemyReviewRunStepRepo"),
    "RunChanges": Adapter("db/run_changes.py", "PostgresRunChanges"),
    "SessionRepo": Adapter("db/repositories.py", "SqlAlchemySessionRepo"),
    "UnitOfWork": Adapter("db/unit_of_work.py", "SqlAlchemyUnitOfWork"),
    "UserRepo": Adapter("db/repositories.py", "SqlAlchemyUserRepo"),
    "VcsGateway": Adapter("vcs/github.py", "GitHubVcsGateway"),
}


def _adapter_source(adapter: Adapter) -> str:
    return (Path("app/infrastructure") / adapter.source).read_text()


def test_every_declared_port_has_an_adapter() -> None:
    """Нет порта без реализации. YAGNI, который можно проверить."""
    assert set(ports.__all__) == set(PORT_TO_ADAPTER), (
        "каждый порт из ports.__all__ должен попасть в PORT_TO_ADAPTER "
        "вместе с адаптером — и наоборот"
    )
    for port, adapter in PORT_TO_ADAPTER.items():
        assert re.search(rf"class {adapter.name}\b", _adapter_source(adapter)), (
            f"{port} has no adapter"
        )


def test_every_adapter_is_wired_in_the_composition_root() -> None:
    """Адаптер собирается только `build_container`: сам или через другой адаптер.

    Репозитории конструируются unit of work, а не контейнером напрямую, поэтому
    проверка транзитивна: достижим из container.py — значится привязанным.
    """
    root = Path("app/infrastructure/container.py").read_text()
    sources = {adapter.name: _adapter_source(adapter) for adapter in PORT_TO_ADAPTER.values()}
    wired = {name for name in sources if re.search(rf"\b{name}\b", root)}
    changed = True
    while changed:
        changed = False
        for name in sources:
            if name in wired:
                continue
            if any(re.search(rf"\b{name}\b", sources[other]) for other in wired):
                wired.add(name)
                changed = True
    assert set(sources) == wired, f"адаптеры вне composition root: {sorted(set(sources) - wired)}"


def test_the_container_is_the_only_place_adapters_are_constructed() -> None:
    """Вызывающий за пределами infrastructure не называет конкретный адаптер."""
    adapter_names = [adapter.name for adapter in PORT_TO_ADAPTER.values()]
    for layer in ("app/api", "app/application", "app/domain"):
        for path in Path(layer).rglob("*.py"):
            text = path.read_text()
            for name in adapter_names:
                assert name not in text, f"{path} names adapter {name} directly"


def test_the_container_hands_out_a_unit_of_work() -> None:
    container = build_container(SETTINGS)
    with container.unit_of_work() as uow:
        for attribute in (
            "repositories",
            "merge_requests",
            "review_runs",
            "context_payloads",
            "findings",
            "published_comments",
            "users",
            "sessions",
        ):
            assert hasattr(uow, attribute)


def test_the_container_hands_out_a_vcs_gateway() -> None:
    """Ленивая выдача: конструирование не касается сети, контракт — порта."""
    container = build_container(SETTINGS)
    gateway = container.vcs_gateway()
    assert callable(gateway.fetch_diff)
    assert callable(gateway.fetch_pr_metadata)
    assert callable(gateway.fetch_file)


def test_the_container_caches_its_lazy_adapters() -> None:
    """Кэш токенов обязан пережить вызов builder'а (D7): иначе каждый вебхук
    получает свежий `GitHubAppAuth` с пустым кэшем и лишним POST к GitHub.
    Удалите `object.__setattr__` в контейнере — тест красный."""
    container = build_container(SETTINGS)
    assert container.vcs_gateway() is container.vcs_gateway()
    assert container.installation_catalog() is container.installation_catalog()


def test_without_a_token_key_the_unit_of_work_refuses_tokens(caplog: pytest.LogCaptureFixture) -> None:
    """Ключ не задан — токены не пишутся (вход откажет). Контейнер собирает и воркер,
    которому ключ не передаётся: предупреждения от контейнера нет."""
    with caplog.at_level(logging.WARNING):
        container = build_container(SETTINGS)
    assert "USER_TOKEN_ENCRYPTION_KEY" not in caplog.text
    assert container.unit_of_work()._cipher is None  # type: ignore[attr-defined]


def test_without_a_token_key_the_api_warns_at_startup(caplog: pytest.LogCaptureFixture) -> None:
    """Вход на таком стенде откажет — это видно в логе старта API."""
    from app.api.factory import create_app

    with caplog.at_level(logging.WARNING, logger="app.api.factory"):
        create_app(SETTINGS)
    assert "USER_TOKEN_ENCRYPTION_KEY не задан" in caplog.text


def test_with_a_token_key_the_unit_of_work_encrypts(caplog: pytest.LogCaptureFixture) -> None:
    key = base64.b64encode(bytes(range(32))).decode()
    with caplog.at_level(logging.WARNING, logger="app.infrastructure.container"):
        container = build_container(SETTINGS.model_copy(update={"user_token_encryption_key": SecretStr(key)}))
    assert "USER_TOKEN_ENCRYPTION_KEY" not in caplog.text
    assert container.unit_of_work()._cipher is not None  # type: ignore[attr-defined]


def test_the_container_hands_out_oauth_providers_by_path_name() -> None:
    container = build_container(SETTINGS)
    github = container.oauth_provider("github")
    assert github is not None
    assert callable(github.exchange_code)
    assert container.oauth_provider("bitbucket") is None


def test_the_container_keeps_one_cache_per_process() -> None:
    """In-process кэш полезен, только если переживает прогон: один объект."""
    container = build_container(SETTINGS)
    assert container.cache_store() is container.cache_store()


def test_the_container_builds_the_parser_once() -> None:
    container = build_container(SETTINGS)
    extractor = container.definition_extractor()
    assert callable(extractor.extract)
    assert container.definition_extractor() is extractor


def test_the_container_hands_out_a_job_queue() -> None:
    """Очередь выдаётся лениво: конструирование не открывает соединение с брокером."""
    queue = build_container(SETTINGS).job_queue()
    assert callable(queue.enqueue)


def test_the_fastapi_dependency_is_port_typed() -> None:
    """Зависимость, которую видит router, обещает порт, а не реализацию."""
    signature = inspect.signature(get_unit_of_work)
    annotation = str(signature.return_annotation)
    assert "UnitOfWork" in annotation
    assert "SqlAlchemy" not in annotation


def _model_settings(**overrides: object) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="test-secret",
        **overrides,  # type: ignore[arg-type]
    )


LOCAL = {"llm_backend": "ollama", "ollama_model": "qwen2.5-coder:7b"}
HOSTED = {
    "eurorouter_base_url": "https://eurorouter.example/v1",
    "eurorouter_model": "mistral-large-2411",
    "eurorouter_api_keys": "sk-secret-one",
}


def _built_gateway_log(caplog: pytest.LogCaptureFixture, **overrides: object) -> str:
    container = build_container(_model_settings(**overrides))
    # Сборка контейнера пишет своё (например, что ключ токенов не задан) — не о модели.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.infrastructure.container"):
        container.llm_gateway()
    return "\n".join(record.getMessage() for record in caplog.records)


def test_stub_is_handed_out_only_when_chosen() -> None:
    gateway = build_container(_model_settings(llm_backend="stub")).llm_gateway()
    assert isinstance(gateway, StubLlmGateway)


def test_container_without_a_chosen_backend_refuses_to_build_a_gateway() -> None:
    with pytest.raises(RuntimeError, match="LLM_BACKEND"):
        build_container(SETTINGS).llm_gateway()


def test_local_model_alone_keeps_the_code_in_our_network(caplog: pytest.LogCaptureFixture) -> None:
    # Ключи Eurorouter заданы, но без флага фоллбэка провайдер не подключается.
    log = _built_gateway_log(caplog, **LOCAL, **HOSTED)
    assert log == "модель: Ollama (qwen2.5-coder:7b); hosted-фоллбэк выключен"


def test_hosted_provider_is_only_ever_the_second(caplog: pytest.LogCaptureFixture) -> None:
    log = _built_gateway_log(caplog, **LOCAL, **HOSTED, llm_hosted_fallback=True)
    assert log == (
        "модель: Ollama (qwen2.5-coder:7b); фоллбэк - Eurorouter (mistral-large-2411): "
        "при сбое локальной модели код уходит hosted-провайдеру"
    )


def test_connection_to_a_model_host_times_out_long_before_the_answer_does() -> None:
    timeout = llm_http_timeout(_model_settings(**LOCAL, llm_timeout_seconds=300))
    assert (timeout.connect, timeout.read) == (10.0, 300)


def test_the_model_gateway_is_built_once() -> None:
    container = build_container(_model_settings(**LOCAL))
    gateway = container.llm_gateway()
    assert isinstance(gateway, FallbackLlmGateway)
    assert container.llm_gateway() is gateway


def test_the_api_vcs_client_keeps_a_short_timeout_and_the_worker_gets_thirty_seconds() -> None:
    """#185: вебхуку GitHub ждёт ответа 10 с - API не ждёт GitHub дольше 5 с и не пережидает
    лимит; воркеру на дифф и публикацию крупного PR - 30 с и минута на лимит."""
    api = build_container(SETTINGS).vcs_gateway()
    worker = build_container(SETTINGS, vcs_limits=WORKER_VCS_LIMITS).vcs_gateway()

    assert api._client.timeout == httpx.Timeout(5.0)  # type: ignore[attr-defined]
    assert api._rate_limit_budget_seconds == 0.0  # type: ignore[attr-defined]
    assert worker._client.timeout == httpx.Timeout(30.0, connect=5.0)  # type: ignore[attr-defined]
    assert worker._rate_limit_budget_seconds == 60.0  # type: ignore[attr-defined]


def test_the_redelivery_pass_gets_a_gateway_that_does_not_wait_out_rate_limits() -> None:
    """Отказ лимита завершает проход повтора доставок: ждать его в consume-цикле нельзя."""
    gateway = build_container(SETTINGS, vcs_limits=WORKER_VCS_LIMITS).redelivery_vcs_gateway()

    assert gateway._rate_limit_budget_seconds == 0.0  # type: ignore[attr-defined]
    assert gateway._client.timeout == httpx.Timeout(5.0)  # type: ignore[attr-defined]
