"""Адаптер `LlmGateway` к hosted-провайдеру по OpenAI-совместимому протоколу (Eurorouter).

`POST /chat/completions` с `response_format` по схеме модели ответа. Строгий
режим схемы выключен: он требует, чтобы каждое поле было обязательным, а у
моделей ответа есть поля со значением по умолчанию; ответ всё равно
проверяет наша модель в `complete_structured` (change `add-llm-gateway`, D1).

Ключей несколько (design D4). Каждый вызов начинает со следующего ключа по
кругу; ключ, отвергнутый провайдером или упёршийся в лимит (`401`, `403`,
`429`), сменяется следующим в том же вызове. Ошибка сервера, таймаут и обрыв
соединения - сбой провайдера сразу: ключ тут ни при чём.

Ключи не попадают ни в текст ошибки, ни в лог: тексты строятся из кода
ответа и номера ключа, а описание ошибки провайдера очищается от ключей.
"""

import itertools
import logging
import threading
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from app.application.ports.llm_gateway import LlmRequest, LlmResponse
from app.domain.entities import PromptMessage
from app.domain.llm_errors import LlmError
from app.infrastructure.llm.structured import RawReply, complete_structured

logger = logging.getLogger(__name__)

# Отказ, за который отвечает ключ, а не провайдер: следующий ключ может пройти.
_KEY_REJECTED = frozenset({401, 403, 429})


class OpenAiCompatibleGateway:
    def __init__(
        self,
        client: httpx.Client,
        model: str,
        keys: Sequence[SecretStr],
        *,
        name: str,
        max_attempts: int,
    ) -> None:
        if not keys:
            raise ValueError(f"{name}: нужен хотя бы один ключ")
        self._client = client
        self._model = model
        self._keys = tuple(keys)
        self._name = name
        self._max_attempts = max_attempts
        self._turns = itertools.count()
        self._lock = threading.Lock()

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        with self._lock:
            first = next(self._turns) % len(self._keys)
        order = [(first + shift) % len(self._keys) for shift in range(len(self._keys))]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": request.response_model.__name__,
                "schema": request.response_model.model_json_schema(),
                "strict": False,
            },
        }

        def send(messages: Sequence[PromptMessage]) -> RawReply:
            body = {
                "model": self._model,
                "messages": [{"role": m.role.value, "content": m.content} for m in messages],
                "response_format": response_format,
                "max_tokens": request.max_output_tokens,
                # Воспроизводимый ответ: eval сравнивает прогоны на одном датасете.
                "temperature": 0,
            }
            return self._post_rotating(body, order)

        return complete_structured(send, request, max_attempts=self._max_attempts)

    def _post_rotating(self, body: dict[str, Any], order: Sequence[int]) -> RawReply:
        rejected: list[int] = []
        for index in order:
            response = self._post(body, self._keys[index])
            if response.status_code in _KEY_REJECTED:
                rejected.append(response.status_code)
                logger.warning(
                    "%s: ключ %d из %d отвергнут (HTTP %d)",
                    self._name,
                    index + 1,
                    len(self._keys),
                    response.status_code,
                )
                continue
            if response.status_code != httpx.codes.OK:
                raise LlmError(
                    f"{self._name}: HTTP {response.status_code}: {self._error_text(response)}"
                )
            return self._reply(response)
        codes = ", ".join(str(code) for code in rejected)
        raise LlmError(f"{self._name}: все ключи отвергнуты (HTTP {codes})")

    def _post(self, body: dict[str, Any], key: SecretStr) -> httpx.Response:
        try:
            return self._client.post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key.get_secret_value()}"},
            )
        except httpx.TimeoutException as error:
            raise LlmError(f"{self._name}: таймаут ({type(error).__name__})") from None
        except httpx.RequestError as error:
            raise LlmError(f"{self._name} недоступен ({type(error).__name__})") from None

    def _reply(self, response: httpx.Response) -> RawReply:
        try:
            data = response.json()
            choice = data["choices"][0]
            text: str = choice["message"]["content"]
            model: str = data["model"]
            tokens: int = data["usage"]["total_tokens"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LlmError(f"{self._name} вернул неожиданное тело на /chat/completions") from error
        # `length` - ответ оборван по `max_tokens`.
        truncated = choice.get("finish_reason") == "length"
        return RawReply(text=text, model=model, tokens=tokens, truncated=truncated)

    def _error_text(self, response: httpx.Response) -> str:
        """Описание ошибки провайдера без ключей: провайдер может процитировать ключ."""
        try:
            error = response.json().get("error")
            message = error.get("message") if isinstance(error, dict) else error
        except (ValueError, AttributeError):
            return "без описания"
        if not message:
            return "без описания"
        text = str(message)[:200]
        for key in self._keys:
            text = text.replace(key.get_secret_value(), "***")
        return text
