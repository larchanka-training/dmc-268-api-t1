"""Порт транспорта к LLM.

Отдельно от `app/application/ports/repositories.py` — не порт хранения, порт
внешней модели. Держится вне `ports.__all__` по той же причине, что и
`JobQueue`: гвардрейл в `tests/test_container.py` заточен под SQLAlchemy-
адаптеры портов хранения.

Порт получает готовый запрос: сообщения собирает чистая функция
(`app/domain/prompt.py`), форму ответа задаёт Pydantic-модель
(`app/application/llm_schema.py`). Адаптер выбирает провайдера, способ
получить ответ по модели (Instructor, tool calling, `format` в Ollama) и
повторные попытки, но исходные сообщения не меняет и не убирает: он может
только дописать свои после них. Сбой транспорта - `LlmError`, ответ, так и
не прошедший модель, - `LlmOutputError` (`app/domain/llm_errors.py`).

Повтор внутри вызова (ответ не по схеме, переход к следующему провайдеру)
адаптер сообщает слушателю `on_retry` из запроса: прогон пишет каждую
попытку отдельным шагом (`review_run_steps`).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

# Находка модели - доменная запись (её разбирает доменный отсев); порт её
# реэкспортирует, поэтому вызывающие импортируют всё о модели отсюда.
from app.domain.entities import DiffAnchor, LlmFinding, LlmRuleRef, PromptMessage
from app.domain.enums import FindingCategory, ThreadFollowUpStatus
from app.domain.llm_errors import LlmError

__all__ = [
    "LlmFinding",
    "LlmGateway",
    "LlmRequest",
    "LlmResponse",
    "LlmReviewResult",
    "LlmReviewSummary",
    "LlmRuleRef",
    "LlmTask",
    "LlmThreadFollowUp",
    "RetryListener",
]


class LlmTask(StrEnum):
    """Какой проход прогона вызывает модель: для логов, метрик и заглушки."""

    REVIEW = "review"
    VERIFY = "verify"


# Слушатель повтора: попытка не удалась (`LlmError` - почему), и вызов
# пробует снова. Второй аргумент - токены, израсходованные вызовом `complete`
# с его начала по эту попытку включительно, а не одной попыткой: так их
# одинаково считают и цикл проверки, и составной фоллбэк поверх него.
# Слушатель не бросает: сбой своей записи он гасит сам, поэтому адаптер зовёт
# его без защиты, и вызов идёт дальше, как без слушателя.
type RetryListener = Callable[[LlmError, int], None]


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


@dataclass(frozen=True, slots=True)
class LlmResponse[T: BaseModel]:
    # Модель, которая фактически ответила: при фоллбэке - резервная.
    model: str
    tokens_used: int
    content: T


class LlmGateway(Protocol):
    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]: ...


@dataclass(frozen=True, slots=True)
class LlmReviewSummary:
    problem: str
    positives: tuple[str, ...]
    effort: str


@dataclass(frozen=True, slots=True)
class LlmThreadFollowUp:
    anchor: DiffAnchor
    category: FindingCategory
    status: ThreadFollowUpStatus
    reply: str
    resolve_thread: bool = False


@dataclass(frozen=True, slots=True)
class LlmReviewResult:
    model: str
    tokens_used: int
    findings: tuple[LlmFinding, ...]
    summary: LlmReviewSummary | None = None
    thread_follow_ups: tuple[LlmThreadFollowUp, ...] = ()
