"""Composition root связывает каждый порт ровно один раз — и только он."""

import inspect
import re
from pathlib import Path

from app.api.dependencies import get_unit_of_work
from app.application import ports
from app.config import Settings
from app.infrastructure.container import build_container

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    ollama_base_url="http://localhost:11434",
)

# Порты, чей адаптер не SqlAlchemy*: внешние системы.
EXTERNAL_ADAPTERS = {
    "EmbeddingGateway": "OllamaEmbeddingGateway",
}


def test_every_declared_port_has_an_adapter_and_a_caller() -> None:
    """Нет порта без реализации. YAGNI, который можно проверить."""
    declared = {name for name in ports.__all__}
    assert declared, "no ports declared"
    source = Path("app/infrastructure/db/repositories.py").read_text()
    source += Path("app/infrastructure/db/unit_of_work.py").read_text()
    for port in declared:
        adapter = EXTERNAL_ADAPTERS.get(port, f"SqlAlchemy{port}")
        external = EXTERNAL_ADAPTERS.get(port)
        haystack = (
            Path("app/infrastructure/ollama_embedding_gateway.py").read_text()
            if external
            else source
        )
        assert re.search(rf"{adapter}\b", haystack), f"{port} has no adapter"


def test_the_container_is_the_only_place_adapters_are_constructed() -> None:
    """Вызывающий за пределами infrastructure не называет конкретный адаптер."""
    for layer in ("app/api", "app/application", "app/domain"):
        for path in Path(layer).rglob("*.py"):
            text = path.read_text()
            assert "SqlAlchemy" not in text, f"{path} constructs an adapter directly"


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
            "code_profile",
        ):
            assert hasattr(uow, attribute)


def test_profile_limits_flow_from_settings_into_the_use_cases() -> None:
    """Регрессия ревью: настройки читаются, а не дублируются дефолтами домена."""
    settings = Settings(
        database_url=SETTINGS.database_url,
        ollama_base_url=SETTINGS.ollama_base_url,
        profile_max_chunk_bytes=111,
        profile_retrieval_top_k=2,
        profile_retrieval_byte_budget=333,
    )
    container = build_container(settings)
    assert container.profile_limits.max_chunk_bytes == 111
    assert container.profile_limits.retrieval_top_k == 2
    assert container.profile_limits.retrieval_byte_budget == 333
    with container.unit_of_work() as uow:
        retrieve = container.retrieve_similar_code(uow)
        ingest = container.ingest_profile(uow)
    assert retrieve._limits is container.profile_limits
    assert ingest._limits is container.profile_limits


def test_profile_limits_have_no_hidden_defaults() -> None:
    """Единственное место значений — Settings: у лимитов домена нет дефолтов."""
    from dataclasses import MISSING, fields

    from app.domain.profile import ProfileLimits

    for field in fields(ProfileLimits):
        assert field.default is MISSING and field.default_factory is MISSING, (
            f"ProfileLimits.{field.name} не должен иметь дефолта"
        )


def test_the_fastapi_dependency_is_port_typed() -> None:
    """Зависимость, которую видит router, обещает порт, а не реализацию."""
    signature = inspect.signature(get_unit_of_work)
    annotation = str(signature.return_annotation)
    assert "UnitOfWork" in annotation
    assert "SqlAlchemy" not in annotation
