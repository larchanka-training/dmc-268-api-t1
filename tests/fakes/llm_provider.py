"""OpenAI-совместимый провайдер модели за `httpx.MockTransport`: граница модели на уровне HTTP.

`ScriptedLlmGateway` подменяет порт целиком. Этот фейк стоит за настоящим
`OpenAiCompatibleGateway`: ротация ключей, разбор `429`, перевод таймаута и
обрыва в `LlmTimeoutError`/`LlmError` и проверка ответа по схеме идут боевым
кодом, а сбои приходят оттуда, откуда приходят в проде, - из транспорта.
"""

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from app.application.ports.llm_gateway import LlmResponse
from app.infrastructure.llm.openai_compatible import OpenAiCompatibleGateway

BASE_URL = "https://llm.test/v1"
MODEL = "chaos-model"

# Ответ сценария: исключение транспорта (или `BaseException` - смерть процесса
# посреди вызова), HTTP-статус отказа, готовый HTTP-ответ или ответ модели.
type Answer = BaseException | int | httpx.Response | LlmResponse[Any]


@dataclass
class FakeLlmProvider:
    """Отвечает на `POST /chat/completions` следующим ответом сценария."""

    answers: list[Answer]
    # Ключ (без `Bearer`) каждого запроса - по нему видна ротация ключей.
    keys_used: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def gateway(self, keys: Sequence[str] = ("key-1",), *, name: str = "eurorouter") -> OpenAiCompatibleGateway:
        client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(self.handle))
        return OpenAiCompatibleGateway(
            client, MODEL, [SecretStr(k) for k in keys], name=name, max_attempts=1
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions"), request.url
        with self._lock:
            self.keys_used.append(request.headers["Authorization"].removeprefix("Bearer "))
            if not self.answers:
                raise AssertionError("неожиданный вызов модели")
            answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": {"message": f"HTTP {answer}"}})
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=_completion(answer.content, answer.tokens_used))

    @property
    def calls(self) -> int:
        return len(self.keys_used)


def _completion(content: BaseModel, tokens: int) -> dict[str, Any]:
    return {
        "model": MODEL,
        "choices": [
            {
                "message": {"role": "assistant", "content": content.model_dump_json(by_alias=True)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": tokens},
    }
