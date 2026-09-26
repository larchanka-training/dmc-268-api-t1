"""Адаптер вложений Ollama на подменённом клиенте: только транспорт.

Живой Ollama не нужен: адаптер не содержит бизнес-правил, и всё, что стоит
проверить, — контракт SDK (модель, батч текстов, порядок ответа) и отказ при
несовпадении размерности со схемой профиля.
"""

from types import SimpleNamespace

import ollama
import pytest

from app.domain.profile import EMBEDDING_DIMENSION
from app.infrastructure.ollama_embedding_gateway import OllamaEmbeddingGateway

MODEL = "nomic-embed-text"


class FakeOllamaClient:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self._embeddings = embeddings
        self.calls: list[tuple[str, list[str]]] = []

    def embed(self, *, model: str, input: list[str]) -> SimpleNamespace:
        self.calls.append((model, list(input)))
        return SimpleNamespace(embeddings=self._embeddings)


def make_gateway(monkeypatch: pytest.MonkeyPatch, embeddings) -> tuple[
    OllamaEmbeddingGateway, FakeOllamaClient
]:
    client = FakeOllamaClient(embeddings)
    monkeypatch.setattr(ollama, "Client", lambda host: client)
    gateway = OllamaEmbeddingGateway(
        base_url="http://ollama:11434", model=MODEL, dimension=EMBEDDING_DIMENSION
    )
    return gateway, client


def test_empty_input_calls_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, client = make_gateway(monkeypatch, [])
    assert gateway.embed([]) == []
    assert client.calls == []


def test_texts_go_as_one_batch_and_vectors_keep_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vectors = [[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)] * 2
    gateway, client = make_gateway(monkeypatch, vectors)
    assert gateway.embed(["def a(): pass", "def b(): pass"]) == vectors
    assert client.calls == [(MODEL, ["def a(): pass", "def b(): pass"])]


def test_a_wrong_dimension_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, _ = make_gateway(monkeypatch, [[0.0, 0.1]])
    with pytest.raises(ValueError, match="dimension"):
        gateway.embed(["def a(): pass"])
