"""Адаптер `LlmGateway` к локальной модели в Ollama - нативная ручка `/api/chat`.

Нативная, а не OpenAI-совместимая `/v1/chat/completions`: только здесь
можно передать окно (`num_ctx`). Без него Ollama работает с небольшим окном
по умолчанию и длинный запрос обрезает с начала - вместе с системным
сообщением, на котором держится защита от инъекций (change `add-llm-gateway`,
design D1).

Адаптер только переводит запрос в протокол Ollama и обратно; проверка ответа
по схеме и повторы - `complete_structured`, общий для всех провайдеров.
"""

from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import BaseModel

from app.application.ports.llm_gateway import LlmRequest, LlmResponse
from app.domain.entities import PromptMessage
from app.domain.llm_errors import LlmError, LlmTimeoutError
from app.infrastructure.llm.structured import RawReply, complete_structured


class OllamaGateway:
    def __init__(
        self, client: httpx.Client, model: str, *, window_tokens: int, max_attempts: int
    ) -> None:
        self._client = client
        self._model = model
        self._window_tokens = window_tokens
        self._max_attempts = max_attempts

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        schema = request.response_model.model_json_schema()

        def send(messages: Sequence[PromptMessage]) -> RawReply:
            return self._chat(messages, schema, request.max_output_tokens)

        return complete_structured(send, request, max_attempts=self._max_attempts)

    def _chat(
        self, messages: Sequence[PromptMessage], schema: dict[str, Any], max_output_tokens: int
    ) -> RawReply:
        body = {
            "model": self._model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "format": schema,
            "stream": False,
            "options": {
                "num_ctx": self._window_tokens,
                "num_predict": max_output_tokens,
                # Воспроизводимый ответ: eval сравнивает прогоны на одном датасете.
                "temperature": 0,
            },
        }
        try:
            response = self._client.post("/api/chat", json=body)
        except httpx.TimeoutException as error:
            raise LlmTimeoutError(f"Ollama: таймаут ({type(error).__name__})") from None
        except httpx.RequestError as error:
            raise LlmError(f"Ollama недоступна ({type(error).__name__})") from None
        if response.status_code != httpx.codes.OK:
            raise LlmError(f"Ollama: HTTP {response.status_code}: {_error_text(response)}")
        try:
            data = response.json()
            text: str = data["message"]["content"]
            model: str = data["model"]
            # С Ollama 0.33.3 поле - весь вход вместе с закэшированной частью;
            # `.get` - на случай, если сервер старше и поле опустил.
            prompt_tokens: int = data.get("prompt_eval_count", 0)
            output_tokens: int = data["eval_count"]
            # `length` - ответ оборван по `num_predict`.
            truncated = data.get("done_reason") == "length"
        except (ValueError, KeyError, TypeError) as error:
            raise LlmError("Ollama вернула неожиданное тело на /api/chat") from error
        # Оценка запроса до отправки - средняя («байты / 4»). Если модель
        # насчитала больше, чем оставляет окно за вычетом резерва, она могла
        # обрезать начало запроса: ответ на неполный запрос не принимаем.
        prompt_limit = self._window_tokens - max_output_tokens
        if prompt_tokens > prompt_limit:
            raise LlmError(
                f"Ollama насчитала в запросе {prompt_tokens} токенов при пределе "
                f"{prompt_limit}: запрос мог быть обрезан",
                tokens_used=prompt_tokens + output_tokens,
            )
        return RawReply(
            text=text, model=model, tokens=prompt_tokens + output_tokens, truncated=truncated
        )


def _error_text(response: httpx.Response) -> str:
    """Текст ошибки Ollama из поля `error`; тело запроса в него не попадает."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return "без описания"
    return str(error)[:200] if error else "без описания"
