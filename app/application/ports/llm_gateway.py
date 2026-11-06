"""Порт транспорта к LLM.

Отдельно от `app/application/ports/repositories.py` — не порт хранения, порт
внешней модели. Входит в `ports.__all__`, адаптер сверяет
`tests/test_container.py`.

Порт получает готовый запрос: сообщения собирает чистая функция
(`app/domain/prompt.py`), форму ответа задаёт Pydantic-модель
(`app/application/llm_schema.py`). Адаптер выбирает провайдера, способ
получить ответ по модели (Instructor, tool calling, `format` в Ollama) и
повторные попытки, но исходные сообщения не меняет и не убирает: он может
только дописать свои после них. Сбой транспорта - `LlmError`, ответ, так и
не прошедший модель, - `LlmOutputError` (`app/domain/llm_errors.py`).

Повтор внутри вызова (ответ не по схеме, переход к следующему провайдеру)
адаптер сообщает слушателю `on_retry` из запроса: прогон пишет каждую
попытку отдельным шагом (`review_run_steps`). Расход каждого ответа
провайдера адаптер пишет в счётчик `meter` из запроса - одна точка учёта на
повторы, смену ключа и фоллбэк; ни ответ, ни сбой расход не несут.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

# Находка модели - доменная запись (её разбирает доменный отсев); порт её
# реэкспортирует, поэтому вызывающие импортируют всё о модели отсюда.
from app.domain.entities import LlmFinding, LlmRuleRef, PromptMessage
from app.domain.llm_errors import LlmError
from app.domain.llm_usage import NO_USAGE, LlmUsage

__all__ = [
    "LlmFinding",
    "LlmGateway",
    "LlmMeter",
    "LlmRequest",
    "LlmResponse",
    "LlmReviewResult",
    "LlmReviewSummary",
    "LlmRuleRef",
    "LlmTask",
    "RetryListener",
]


class LlmTask(StrEnum):
    """Какой проход прогона вызывает модель: для логов, метрик и заглушки."""

    REVIEW = "review"
    VERIFY = "verify"


# Слушатель повтора: попытка не удалась (`LlmError` - почему), и вызов
# пробует снова. Расход попытки к этому моменту уже в счётчике запроса.
# Слушатель не бросает: сбой своей записи он гасит сам, поэтому адаптер зовёт
# его без защиты, и вызов идёт дальше, как без слушателя.
type RetryListener = Callable[[LlmError], None]


class LlmMeter:
    """Сумма расхода ответов провайдеров, полученных под одним счётчиком.

    Пишет только адаптер - по разу на каждый ответ провайдера; читает
    вызывающий, когда ему нужен итог. Вызовы модели прогона идут по очереди,
    поэтому блокировки нет.
    """

    def __init__(self) -> None:
        self.total: LlmUsage = NO_USAGE

    def record(self, usage: LlmUsage) -> None:
        self.total += usage


@dataclass(frozen=True, slots=True)
class LlmRequest[T: BaseModel]:
    task: LlmTask
    messages: tuple[PromptMessage, ...]
    response_model: type[T]
    # Предел длины ответа - резерв окна модели под ответ; адаптер передаёт его
    # провайдеру, чтобы ответ не вытеснил запрос из окна.
    max_output_tokens: int
    # Кому сообщить о повторе внутри вызова; `None` - никому. Не часть
    # содержания запроса, поэтому в сравнении не участвует.
    on_retry: RetryListener | None = field(default=None, compare=False)
    # Куда записать расход ответов провайдеров; `None` - не учитывать. Как и
    # слушатель, не часть содержания запроса.
    meter: LlmMeter | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class LlmResponse[T: BaseModel]:
    # Модель, которая фактически ответила: при фоллбэке - резервная.
    model: str
    content: T


class LlmGateway(Protocol):
    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]: ...


@dataclass(frozen=True, slots=True)
class LlmReviewSummary:
    problem: str
    positives: tuple[str, ...]
    effort: str


@dataclass(frozen=True, slots=True)
class LlmReviewResult:
    model: str
    findings: tuple[LlmFinding, ...]
    summary: LlmReviewSummary | None = None
