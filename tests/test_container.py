"""Composition root связывает каждый порт ровно один раз — и только он."""

import inspect
import re
from dataclasses import dataclass
from pathlib import Path

from app.api.dependencies import get_unit_of_work
from app.application import ports
from app.config import Settings
from app.infrastructure.container import build_container

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
    "ContextPayloadRepo": Adapter(
        "db/repositories.py", "SqlAlchemyContextPayloadRepo"
    ),
    "FindingRepo": Adapter("db/repositories.py", "SqlAlchemyFindingRepo"),
    "JobQueue": Adapter("queue/rabbitmq.py", "RabbitMQJobQueue"),
    "LlmGateway": Adapter("llm/stub.py", "StubLlmGateway"),
    "MergeRequestRepo": Adapter("db/repositories.py", "SqlAlchemyMergeRequestRepo"),
    "PublishedCommentRepo": Adapter(
        "db/repositories.py", "SqlAlchemyPublishedCommentRepo"
    ),
    "RepositoryRepo": Adapter("db/repositories.py", "SqlAlchemyRepositoryRepo"),
    "ReviewRunRepo": Adapter("db/repositories.py", "SqlAlchemyReviewRunRepo"),
    "UnitOfWork": Adapter("db/unit_of_work.py", "SqlAlchemyUnitOfWork"),
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
        ):
            assert hasattr(uow, attribute)


def test_the_container_hands_out_a_vcs_gateway() -> None:
    """Ленивая выдача: конструирование не касается сети, контракт — порта."""
    container = build_container(SETTINGS)
    gateway = container.vcs_gateway()
    assert callable(gateway.fetch_diff)
    assert callable(gateway.fetch_pr_metadata)


def test_the_container_caches_its_lazy_adapters() -> None:
    """Кэш токенов обязан пережить вызов builder'а (D7): иначе каждый вебхук
    получает свежий `GitHubAppAuth` с пустым кэшем и лишним POST к GitHub.
    Удалите `object.__setattr__` в контейнере — тест красный."""
    container = build_container(SETTINGS)
    assert container.vcs_gateway() is container.vcs_gateway()


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
