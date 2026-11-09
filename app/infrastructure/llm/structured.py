"""Ответ модели по схеме запроса: общий цикл всех адаптеров провайдеров.

Адаптер переводит сообщения в протокол провайдера и обратно (`send`), а
проверка ответа моделью из запроса, повторы и учёт расхода живут здесь один
раз (change `add-llm-gateway`, design D2). Поэтому свойства порта держатся в
любом адаптере: исходные сообщения не меняются и не удаляются, повтор лишь
дописывает одно сообщение после них.

Повтор не возвращает модели её отвергнутый ответ и не цитирует значения из
него: ответ написан моделью, читавшей код автора PR, и вернулся бы в запрос
мимо экранирования маркеров. В сообщении повтора и в тексте сбоя - только
путь к полю и вид нарушения.

Расход каждого ответа `send` пишется в счётчик запроса здесь и только здесь
(change `run-token-cost`, D3): у каждого провайдера фоллбэка свой цикл, а ключ,
отвергнутый без ответа модели, ответа не даёт, поэтому повтор по схеме, смена
ключа и фоллбэк учитываются ровно по разу без кода в этих местах.
"""

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ValidationError

from app.application.ports.llm_gateway import LlmRequest, LlmResponse
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.domain.llm_errors import LlmError, LlmOutputError
from app.domain.llm_usage import LlmUsage

logger = logging.getLogger(__name__)

# Колонки токенов - bigint; больше не влезло бы в запись прогона уже после оплаты.
_TOKEN_LIMIT = 2**63


@dataclass(frozen=True, slots=True)
class RawReply:
    """Ответ провайдера до проверки: текст, ответившая модель, расход вызова.

    `truncated` - модель оборвала ответ по пределу длины: повтор с тем же
    пределом оборвался бы снова. `rejected` - ответ получен и оплачен, но
    адаптер его не принял: цикл записывает расход и бросает этот сбой.
    """

    text: str
    model: str
    usage: LlmUsage
    truncated: bool = False
    rejected: LlmError | None = None


type Send = Callable[[Sequence[PromptMessage]], RawReply]


def token_count(value: object) -> int:
    """Число токенов из ответа провайдера: нет поля или `null` - 0.

    Целое в записи с дробной частью (`12.0`: у OpenAI-совместимого адаптера
    `Decimal`, у Ollama float) - это целое. Остальное кривое (строка, `true`,
    дробь, минус, больше `bigint` колонки) - предупреждение и 0, как у стоимости:
    ответ уже оплачен, а отказ отправил бы повтор или фоллбэк и заплатил второй раз.
    """
    if value is None:
        return 0
    if type(value) is int and 0 <= value < _TOKEN_LIMIT:
        return value
    if (
        isinstance(value, Decimal | float)
        and math.isfinite(value)
        and 0 <= value < _TOKEN_LIMIT
        and value == int(value)
    ):
        return int(value)
    logger.warning("число токенов в ответе провайдера не разобрано (%s), учтено как 0", type(value).__name__)
    return 0


def complete_structured[T: BaseModel](
    send: Send, request: LlmRequest[T], *, max_attempts: int
) -> LlmResponse[T]:
    """Получить ответ по `request.response_model` не больше чем за `max_attempts` вызовов.

    Сбой транспорта (`LlmError` из `send`) не повторяется: это решение
    адаптера и составного фоллбэка, а не цикла проверки. Расход каждого
    ответа уходит в `request.meter` до его проверки. Перед каждым повтором
    цикл сообщает `request.on_retry`, что попытка не прошла схему.
    """
    messages: tuple[PromptMessage, ...] = request.messages
    violations: list[str] = []
    for attempt in range(1, max_attempts + 1):
        reply = send(messages)
        if request.meter is not None:
            request.meter.record(reply.usage)
        if reply.rejected is not None:
            raise reply.rejected
        try:
            content = request.response_model.model_validate_json(reply.text)
        except ValidationError as error:
            # Оборванный JSON валидным не бывает, а полный ответ провайдер
            # тоже помечает обрезанным, если модель дописала после объекта
            # пробелы до предела: признак решает только судьбу непрошедшего.
            # Такой ответ повторяется с подсказкой о пределе, а не «ответ не
            # JSON»: живой прогон показал, что второй ответ укладывается.
            if reply.truncated:
                violations = [f"ответ обрезан пределом длины ({request.max_output_tokens} токенов)"]
            else:
                violations = _violations(error)
            if attempt < max_attempts and request.on_retry is not None:
                request.on_retry(LlmOutputError("; ".join(violations)))
            messages = (*request.messages, _retry_message(violations, shorter=reply.truncated))
            continue
        return LlmResponse(model=reply.model, content=content)
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


def _retry_message(violations: Sequence[str], *, shorter: bool = False) -> PromptMessage:
    listed = "\n".join(f"- {violation}" for violation in violations)
    ask = "Ответь заново одним JSON-объектом по схеме" + (", короче." if shorter else ".")
    return PromptMessage(
        PromptRole.USER, f"Предыдущий ответ не прошёл проверку схемы:\n{listed}\n\n{ask}"
    )
