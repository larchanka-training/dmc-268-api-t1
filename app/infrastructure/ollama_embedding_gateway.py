"""Адаптер вложений Ollama: только транспорт.

Никаких бизнес-правил: списки текстов на входе — списки векторов на выходе,
в том же порядке. Размерность ответа сверяется со схемой профиля, потому что
несовпадение иначе всплыло бы много позже — на вставке в vector-столбец.
"""

from collections.abc import Sequence

import ollama


class OllamaEmbeddingGateway:
    """Порт `EmbeddingGateway` на Ollama SDK."""

    def __init__(self, base_url: str, model: str, dimension: int) -> None:
        self._client = ollama.Client(host=base_url)
        self._model = model
        self._dimension = dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embed(model=self._model, input=list(texts))
        vectors = [list(vector) for vector in response.embeddings]
        for vector in vectors:
            if len(vector) != self._dimension:
                raise ValueError(
                    f"embedding model {self._model!r} returned dimension "
                    f"{len(vector)}, expected {self._dimension} "
                    "(EMBEDDING_DIMENSION in app.domain.profile): "
                    "the model disagrees with the profile schema"
                )
        return vectors
