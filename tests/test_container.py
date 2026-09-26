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


def test_the_fastapi_dependency_is_port_typed() -> None:
    """Зависимость, которую видит router, обещает порт, а не реализацию."""
    signature = inspect.signature(get_unit_of_work)
    annotation = str(signature.return_annotation)
    assert "UnitOfWork" in annotation
    assert "SqlAlchemy" not in annotation
