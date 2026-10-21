"""Запись шагов одного прогона: начало и исход каждого этапа.

Шаг — данные продукта для «хода прогона» в кабинете, их источник — база
(`review_run_steps`), а не логи. Каждое начало и каждый исход — свой вход в
`with uow:` и свой `commit`, как переходы состояния в `review_pipeline`:
кабинет видит идущий шаг, а сбой следующего шага не откатывает записанное.

Повтор этапа — отдельный шаг того же этапа: `retry` подходит как слушатель
повтора порта модели (`RetryListener`), закрывает неудачную попытку упавшей
и открывает новую. Токены вызова модели адаптер сообщает нарастающим итогом
от начала вызова; шаг получает свою долю — разность с уже записанным.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.application.ports import UnitOfWork
from app.domain.entities import ReviewRunStep
from app.domain.enums import ReviewRunStage
from app.domain.llm_errors import LlmError, PromptTooLargeError
from app.domain.run_steps import error_code, finish_step, start_step

logger = logging.getLogger(__name__)


def spent_tokens(error: BaseException) -> int | None:
    """Токены, которые вызов модели израсходовал к сбою; `None` — модель не вызывалась.

    `PromptTooLargeError` бросается до отправки: модели шаг не вызывал.
    """
    if isinstance(error, PromptTooLargeError) or not isinstance(error, LlmError):
        return None
    return error.tokens_used


class StepRecorder:
    """Шаги одного прогона. Открыт не больше одного шага за раз."""

    def __init__(
        self,
        uow: UnitOfWork,
        review_run_id: UUID,
        *,
        now: Callable[[], datetime],
        monotonic: Callable[[], float],
        new_id: Callable[[], UUID],
    ) -> None:
        self._uow = uow
        self._run_id = review_run_id
        self._now = now
        self._monotonic = monotonic
        self._new_id = new_id
        self._step: ReviewRunStep | None = None
        self._started = 0.0
        # Токены текущего вызова модели, уже записанные в шаги этого этапа.
        self._recorded_tokens = 0

    def start(self, stage: ReviewRunStage, *, at: datetime | None = None) -> None:
        """Открыть шаг этапа. `at` — начало, если вызывающий уже прочёл часы."""
        self._recorded_tokens = 0
        self._open(stage, self._now() if at is None else at)

    def finish(
        self, *, tokens_used: int | None = None, error_code: str | None = None
    ) -> None:
        """Закрыть открытый шаг: с `error_code` — упавшим, без него — завершённым.

        `tokens_used` — нарастающий итог вызова модели этапа; `None` — шаг
        модель не вызывал.
        """
        step = self._current()
        closed = finish_step(
            step,
            duration_seconds=self._monotonic() - self._started,
            tokens_used=self._share(tokens_used),
            error_code=error_code,
        )
        with self._uow:
            self._uow.review_run_steps.update(closed)
            self._uow.commit()
        self._step = None

    def retry(self, error: LlmError, tokens_so_far: int) -> None:
        """Попытка вызова модели не удалась, вызов пробует снова: новый шаг того же этапа."""
        stage = self._current().stage
        self.finish(tokens_used=tokens_so_far, error_code=error.code)
        self._open(stage, self._now())

    def fail(self, error: BaseException) -> None:
        """Закрыть открытый шаг упавшим из-за `error`; нет открытого — ничего.

        Зовётся на пути сбоя прогона, поэтому сбой этой записи логируется и
        не подменяет исходное исключение. Шаг останется `running`.
        """
        if self._step is None:
            return
        try:
            self.finish(tokens_used=spent_tokens(error), error_code=error_code(error))
        except Exception:
            logger.exception(
                "не удалось закрыть шаг %s прогона %s", self._step.id, self._run_id
            )

    def _open(self, stage: ReviewRunStage, started_at: datetime) -> None:
        step = start_step(
            self._run_id, stage, started_at=started_at, step_id=self._new_id()
        )
        with self._uow:
            self._uow.review_run_steps.add(step)
            self._uow.commit()
        # Открытым шаг считается только записанный: иначе закрывать нечего.
        self._step = step
        self._started = self._monotonic()

    def _current(self) -> ReviewRunStep:
        if self._step is None:
            raise RuntimeError(f"у прогона {self._run_id} нет открытого шага")
        return self._step

    def _share(self, tokens_so_far: int | None) -> int | None:
        if tokens_so_far is None:
            return None
        share = max(tokens_so_far - self._recorded_tokens, 0)
        self._recorded_tokens = max(tokens_so_far, self._recorded_tokens)
        return share
