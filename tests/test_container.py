"""The composition root binds every port exactly once, and nothing else does."""

import inspect
import re
from pathlib import Path

from app.api.dependencies import get_unit_of_work
from app.application import ports
from app.config import Settings
from app.infrastructure.container import build_container

SETTINGS = Settings(database_url="postgresql+psycopg://test:test@localhost/test")


def test_every_declared_port_has_an_adapter_and_a_caller() -> None:
    """No port without an implementation. YAGNI, made checkable."""
    declared = {name for name in ports.__all__}
    assert declared, "no ports declared"
    source = Path("app/infrastructure/db/repositories.py").read_text()
    source += Path("app/infrastructure/db/unit_of_work.py").read_text()
    for port in declared:
        assert re.search(rf"SqlAlchemy{port}\b", source), f"{port} has no adapter"


def test_the_container_is_the_only_place_adapters_are_constructed() -> None:
    """A caller outside infrastructure must never name a concrete adapter."""
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
        ):
            assert hasattr(uow, attribute)


def test_the_fastapi_dependency_is_port_typed() -> None:
    """The router-facing dependency promises a port, not an implementation."""
    signature = inspect.signature(get_unit_of_work)
    annotation = str(signature.return_annotation)
    assert "UnitOfWork" in annotation
    assert "SqlAlchemy" not in annotation
