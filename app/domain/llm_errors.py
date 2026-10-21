"""Общая шапка сбоев модели.

Живёт в домене по той же причине, что и `vcs_errors`: слой приложения ловит
её, не завися от инфраструктуры, а адаптеры провайдеров бросают её или её
подклассы, не завися от слоя приложения.

`code` у каждого класса — машиночитаемый код сбоя из каталога ошибок прогона
(§8 PIPELINE_SPEC.md); его пишет шаг прогона (`review_run_steps.error_code`).
"""

from typing import ClassVar


class LlmError(RuntimeError):
    """Модель недоступна или ответила не так, как требует запрос.

    `tokens_used` - сколько вызов успел израсходовать до сбоя: модель могла
    ответить, а ответ не принят. Прогон учитывает их в затратах. Сбой до
    ответа модели - 0.
    """

    code: ClassVar[str] = "LLM_UNAVAILABLE"

    def __init__(self, message: str = "", *, tokens_used: int = 0) -> None:
        super().__init__(message)
        self.tokens_used = tokens_used


class LlmOutputError(LlmError):
    """Ответ модели так и не прошёл описание ожидаемого ответа.

    Адаптер бросает её после своих повторных попыток; текст - ошибка
    валидации, по которой видно поле и значение.
    """

    code: ClassVar[str] = "LLM_INVALID_OUTPUT"


class LlmTimeoutError(LlmError):
    """Провайдер не ответил за таймаут вызова."""

    code: ClassVar[str] = "LLM_TIMEOUT"


class LlmRateLimitedError(LlmError):
    """Провайдер отказал по лимиту запросов (HTTP 429) на каждом ключе вызова."""

    code: ClassVar[str] = "LLM_RATE_LIMITED"


class PromptTooLargeError(LlmError):
    """Запрос вместе с резервом ответа не помещается в окно модели.

    Бросается до отправки: запрос, который модель обрезала бы, не уходит ни
    одному провайдеру. Подкласс `LlmError`, поэтому пайплайн обрабатывает его
    как любой сбой модели: ревью - `failed`, верификатор - `unavailable`.

    Код — свой, `PROMPT_TOO_LARGE`, а не `LLM_*`: модель тут ни при чём,
    запрос не ушёл никому.
    """

    code: ClassVar[str] = "PROMPT_TOO_LARGE"
