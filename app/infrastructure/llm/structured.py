"""Ответ модели по схеме запроса: общий цикл всех адаптеров провайдеров.

Адаптер переводит сообщения в протокол провайдера и обратно (`send`), а
проверка ответа моделью из запроса, повторы и учёт токенов живут здесь один
раз (change `add-llm-gateway`, design D2). Поэтому свойства порта держатся в
любом адаптере: исходные сообщения не меняются и не удаляются, повтор лишь
дописывает одно сообщение после них.

Повтор не возвращает модели её отвергнутый ответ и не цитирует значения из
него: ответ написан моделью, читавшей код автора PR, и вернулся бы в запрос
мимо экранирования маркеров. В сообщении повтора и в тексте сбоя - только
путь к полю и вид нарушения.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from app.application.ports.llm_gateway import LlmRequest, LlmResponse
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.domain.llm_errors import LlmOutputError


@dataclass(frozen=True, slots=True)
class RawReply:
    """Ответ провайдера до проверки: текст, ответившая модель, токены вызова."""

    text: str
    model: str
    tokens: int


type Send = Callable[[Sequence[PromptMessage]], RawReply]


def complete_structured[T: BaseModel](
    send: Send, request: LlmRequest[T], *, max_attempts: int
) -> LlmResponse[T]:
    """Получить ответ по `request.response_model` не больше чем за `max_attempts` вызовов.

    Сбой транспорта (`LlmError` из `send`) не повторяется: это решение
    адаптера и составного фоллбэка, а не цикла проверки.
    """
    messages: tuple[PromptMessage, ...] = request.messages
    tokens = 0
    violations: list[str] = []
    for _ in range(max_attempts):
        reply = send(messages)
        tokens += reply.tokens
        try:
            content = request.response_model.model_validate_json(reply.text)
        except ValidationError as error:
            violations = _violations(error)
            messages = (*request.messages, _retry_message(violations))
            continue
        return LlmResponse(model=reply.model, tokens_used=tokens, content=content)
    raise LlmOutputError(
        f"ответ модели не прошёл схему (попыток: {max_attempts}): {'; '.join(violations)}"
    )


def _violations(error: ValidationError) -> list[str]:
    """«путь: вид» по каждой ошибке - без значений из ответа."""
    found: list[str] = []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        if item["type"] == "json_invalid":
            found.append("ответ не JSON")
        else:
            found.append(f"{'.'.join(str(part) for part in item['loc'])}: {item['type']}")
    return found


def _retry_message(violations: Sequence[str]) -> PromptMessage:
    listed = "\n".join(f"- {violation}" for violation in violations)
    return PromptMessage(
        PromptRole.USER,
        f"Предыдущий ответ не прошёл проверку схемы:\n{listed}\n\n"
        "Ответь заново одним JSON-объектом по схеме.",
    )
