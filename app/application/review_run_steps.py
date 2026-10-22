"""Запись шагов одного прогона: начало и исход каждого этапа.

Шаг — данные продукта для «хода прогона» в кабинете, их источник — база
(`review_run_steps`), а не логи. Каждое начало и каждый исход — свой вход в
`with uow:` и свой `commit`, как переходы состояния в `review_pipeline`:
кабинет видит идущий шаг, а сбой следующего шага не откатывает записанное.
Журнал вторичен: сбой его записи логируется и не меняет ход прогона — не
переводит его в `failed` (комментарии могли уже уйти в VCS) и не прерывает
вызов модели.

Повтор этапа — отдельный шаг того же этапа: `retry` подходит как слушатель
повтора порта модели (`RetryListener`), закрывает неудачную попытку упавшей
и открывает новую. Токены вызова модели адаптер сообщает нарастающим итогом
от начала вызова; шаг получает свою долю — разность с уже записанным.

Шаг — ещё и спан трассировки (`StageTracer`): спан открывается вместе с шагом и
закрывается вместе с его исходом. Это телеметрия для команды; источником шагов
для API остаётся база.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.application.ports import UnitOfWork
from app.application.ports.stage_tracer import NO_STAGE_TRACING, StageSpan, StageTracer
from app.domain.entities import ReviewRunStep
from app.domain.enums import ReviewRunStage
from app.domain.llm_errors import LlmError, PromptTooLargeError
from app.domain.run_steps import abandon_steps, error_code, finish_step, start_step

logger = logging.getLogger(__name__)


def spent_tokens(error: BaseException) -> int | None:
    """Токены, которые вызов модели израсходовал к сбою; `None` — модель не вызывалась.

    `PromptTooLargeError` бросается до отправки: модели шаг не вызывал.
    """
    if isinstance(error, PromptTooLargeError) or not isinstance(error, LlmError):
        return None
    return error.tokens_used


def close_abandoned_steps(uow: UnitOfWork, review_run_id: UUID, *, now: datetime) -> None:
    """Закрыть шаги упавшего прогона, оставшиеся `running`, своим коммитом.

    Зовётся после перевода прогона в `failed`: шаг брошенной попытки (воркер
    умер, сообщение доставлено снова или выметено) или шаг, чей исход не
    записался, иначе остался бы `running` навсегда. Отдельный коммит, а не
    общий с прогоном: журнал вторичен, и сбой его записи не должен мешать
    перевести прогон в `failed` — он логируется и наружу не выходит.
    """
    try:
        with uow:
            for step in abandon_steps(uow.review_run_steps.list_for_run(review_run_id), now=now):
                uow.review_run_steps.update(step)
            uow.commit()
    except Exception:
        logger.exception("не удалось закрыть брошенные шаги прогона %s", review_run_id)


class StepRecorder:
    """Шаги одного прогона. Открыт не больше одного этапа за раз.

    Сбой записи шага логируется и наружу не выходит. Этап при этом остаётся
    открытым, и следующий `finish` или `retry` его закрывает: шаг, чьё начало
    не записалось, просто не закрывается (закрывать нечего), а незакрытый
    из-за сбоя шаг остаётся `running`.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        review_run_id: UUID,
        *,
        now: Callable[[], datetime],
        monotonic: Callable[[], float],
        new_id: Callable[[], UUID],
        tracer: StageTracer = NO_STAGE_TRACING,
    ) -> None:
        self._uow = uow
        self._run_id = review_run_id
        self._now = now
        self._monotonic = monotonic
        self._new_id = new_id
        # Открытый этап и его записанный шаг: шага нет, если не записалось начало.
        self._stage: ReviewRunStage | None = None
        self._step: ReviewRunStep | None = None
        # Спан открытого шага: открывается и закрывается в тех же точках, что шаг.
        self._tracer = tracer
        self._span: StageSpan | None = None
        self._started = 0.0
        # Токены текущего вызова модели, уже отнесённые к шагам этого этапа.
        self._recorded_tokens = 0

    def start(self, stage: ReviewRunStage, *, at: datetime | None = None) -> None:
        """Открыть шаг этапа. `at` — начало, если вызывающий уже прочёл часы."""
        self._stage = stage
        self._recorded_tokens = 0
        self._open(self._now() if at is None else at)

    def finish(
        self, *, tokens_used: int | None = None, error_code: str | None = None
    ) -> None:
        """Закрыть открытый этап: с `error_code` — упавшим, без него — завершённым.

        `tokens_used` — нарастающий итог вызова модели этапа; `None` — шаг
        модель не вызывал.
        """
        self._close(tokens_used=tokens_used, error_code=error_code)
        self._stage = None

    def retry(self, error: LlmError, tokens_so_far: int) -> None:
        """Попытка вызова модели не удалась, вызов пробует снова: новый шаг того же этапа.

        Зовётся изнутри вызова модели, поэтому сбой записи не выходит наружу:
        вызов уходит следующей попытке или провайдеру, как без журнала.
        """
        self._close(tokens_used=tokens_so_far, error_code=error.code)
        self._open(self._now())

    def fail(self, error: BaseException) -> None:
        """Закрыть открытый этап упавшим из-за `error`; нет открытого — ничего.

        Зовётся на пути сбоя прогона; сбой записи, как любой другой, только
        логируется и не подменяет исходное исключение. Шаг останется `running`.
        """
        if self._stage is not None:
            self.finish(tokens_used=spent_tokens(error), error_code=error_code(error))

    def interrupt(self, error: BaseException) -> None:
        """Закрыть спан открытого этапа упавшим: прогон прерван не `Exception`.

        `KeyboardInterrupt` или `SystemExit` завершают процесс: шаг и прогон в базе
        не трогаются — прогон переведёт в `failed` выметатель, как после смерти
        воркера. Без этого спан этапа не закрылся бы и не попал в трейс.
        """
        self._end_span(error_code(error))

    def _open(self, started_at: datetime) -> None:
        stage = self._current_stage()
        # Прежний этап не закрыли (`start` без `finish`): его спан закрывается здесь,
        # до нового, иначе он не закрылся бы никогда, а снятие контекста нарушило бы
        # порядок. Шаг этого этапа, как при сбое записи, остаётся `running`.
        self._end_span(None)
        # Спан — телеметрия, а не журнал: открывается, даже если шаг не запишется.
        self._span = self._tracer.start(self._run_id, stage)
        step = start_step(self._run_id, stage, started_at=started_at, step_id=self._new_id())
        if not self._write(step, "начало", lambda: self._uow.review_run_steps.add(step)):
            return
        # Открытым шаг считается только записанный: иначе закрывать нечего.
        self._step = step
        self._started = self._monotonic()

    def _end_span(self, error_code: str | None) -> None:
        # Повторное закрытие спан уже не найдёт.
        span, self._span = self._span, None
        if span is not None:
            span.end(error_code)

    def _close(self, *, tokens_used: int | None, error_code: str | None) -> None:
        self._current_stage()
        # Доля считается и для шага, который не записан: иначе токены этой
        # попытки достались бы следующей.
        share = self._share(tokens_used)
        step, self._step = self._step, None
        try:
            if step is None:
                return
            closed = finish_step(
                step,
                duration_seconds=self._monotonic() - self._started,
                tokens_used=share,
                error_code=error_code,
            )
            self._write(closed, "исход", lambda: self._uow.review_run_steps.update(closed))
        finally:
            # Спан закрывается после записи исхода — её запросы к базе ложатся
            # в спан этапа — и при любом её исходе.
            self._end_span(error_code)

    def _write(self, step: ReviewRunStep, what: str, write: Callable[[], None]) -> bool:
        """Записать шаг своим коммитом; сбой — в лог, наружу не выходит."""
        try:
            with self._uow:
                write()
                self._uow.commit()
        except Exception:
            logger.exception(
                "не удалось записать %s шага %s прогона %s", what, step.id, self._run_id
            )
            return False
        return True

    def _current_stage(self) -> ReviewRunStage:
        if self._stage is None:
            raise RuntimeError(f"у прогона {self._run_id} нет открытого этапа")
        return self._stage

    def _share(self, tokens_so_far: int | None) -> int | None:
        if tokens_so_far is None:
            return None
        share = max(tokens_so_far - self._recorded_tokens, 0)
        self._recorded_tokens = max(tokens_so_far, self._recorded_tokens)
        return share
