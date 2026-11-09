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
и открывает новую. Расход модели адаптер пишет в счётчик прогона (`LlmMeter`);
шаг, вызывавший модель, получает свою долю — разность итога счётчика с уже
отнесённым к прежним шагам.

Шаг — ещё и спан трассировки (`StageTracer`): спан открывается вместе с шагом и
закрывается вместе с его исходом. Это телеметрия для команды; источником шагов
для API остаётся база.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from uuid import UUID

from app.application.ports import UnitOfWork
from app.application.ports.llm_gateway import LlmMeter
from app.application.ports.stage_tracer import NO_STAGE_TRACING, StageSpan, StageTracer
from app.domain.entities import ReviewRunStep
from app.domain.enums import ReviewRunStage
from app.domain.llm_errors import LlmError, PromptTooLargeError
from app.domain.run_steps import (
    ABANDONED,
    abandon_steps,
    error_code,
    finish_step,
    start_step,
)

logger = logging.getLogger(__name__)


def called_model(error: BaseException) -> bool:
    """Вызывал ли модель шаг, упавший с `error`.

    `PromptTooLargeError` бросается до отправки: модели шаг не вызывал.
    """
    return isinstance(error, LlmError) and not isinstance(error, PromptTooLargeError)


def close_abandoned_steps(
    uow: UnitOfWork,
    review_run_id: UUID,
    *,
    now: datetime,
    known: Mapping[UUID, ReviewRunStep] | None = None,
) -> None:
    """Закрыть шаги терминального прогона, оставшиеся `running`, своим коммитом.

    Зовётся после перевода прогона в `failed` или `completed`: шаг брошенной
    попытки (воркер умер, и прогон выметен) или шаг,
    чей исход не записался, иначе остался бы `running` навсегда. Исход,
    который воркер знает (`StepRecorder.unwritten`), записывается как есть,
    прочие шаги закрываются `REVIEW_RUN_STALE`. Отдельный коммит, а не общий
    с прогоном: журнал вторичен, и сбой его записи не должен мешать перевести
    прогон в терминальное состояние — он логируется и наружу не выходит.
    """
    try:
        with uow:
            for step in abandon_steps(
                uow.review_run_steps.list_for_run(review_run_id), now=now, known=known
            ):
                uow.review_run_steps.update(step)
            uow.commit()
    except Exception:
        # Идентификатор — атрибутом записи, не текстом (правило «Логи»): из
        # пайплайна его приносит контекст сообщения воркера, из sweep-пути
        # контекста нет — здесь его несёт extra. Имя — то же, что у атрибута
        # спанов (REVIEW_RUN_ID в telemetry.py); взять константу из
        # инфраструктуры слой приложения не может — зависимости направлены
        # внутрь.
        logger.exception(
            "не удалось закрыть брошенные шаги прогона",
            extra={"review_run_id": str(review_run_id)},
        )


class StepRecorder:
    """Шаги одного прогона. Открыт не больше одного этапа за раз.

    Сбой записи шага логируется и наружу не выходит. Этап при этом остаётся
    открытым, и следующий `finish` или `retry` его закрывает: шаг, чьё начало
    не записалось, просто не закрывается (закрывать нечего), а незакрытый
    из-за сбоя шаг остаётся `running`, и его исход хранится в `unwritten` до
    `close_abandoned_steps`.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        review_run_id: UUID,
        *,
        now: Callable[[], datetime],
        monotonic: Callable[[], float],
        new_id: Callable[[], UUID],
        meter: LlmMeter,
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
        # Счётчик расхода модели прогона и его токены, уже отнесённые к шагам.
        self._meter = meter
        self._recorded_tokens = 0
        # Исходы шагов, которые не записались: их пишет `close_abandoned_steps`.
        self._unwritten: dict[UUID, ReviewRunStep] = {}

    @property
    def unwritten(self) -> Mapping[UUID, ReviewRunStep]:
        """Исходы закрытых шагов, чья запись не удалась, по id шага."""
        return dict(self._unwritten)

    def start(self, stage: ReviewRunStage, *, at: datetime | None = None) -> None:
        """Открыть шаг этапа. `at` — начало, если вызывающий уже прочёл часы."""
        self._stage = stage
        self._open(self._now() if at is None else at)

    def finish(self, *, used_model: bool = False, error_code: str | None = None) -> None:
        """Закрыть открытый этап: с `error_code` — упавшим, без него — завершённым.

        `used_model` — шаг вызывал модель: его токены — доля счётчика; иначе
        токенов у шага нет (`None`).
        """
        self._close(used_model=used_model, error_code=error_code)
        self._stage = None

    def retry(self, error: LlmError) -> None:
        """Попытка вызова модели не удалась, вызов пробует снова: новый шаг того же этапа.

        Зовётся изнутри вызова модели, поэтому сбой записи не выходит наружу:
        вызов уходит следующей попытке или провайдеру, как без журнала.
        """
        self._close(used_model=True, error_code=error.code)
        self._open(self._now())

    def fail(self, error: BaseException) -> None:
        """Закрыть открытый этап упавшим из-за `error`; нет открытого — ничего.

        Зовётся на пути сбоя прогона; сбой записи, как любой другой, только
        логируется и не подменяет исходное исключение. Шаг останется `running`.
        """
        if self._stage is not None:
            self.finish(used_model=called_model(error), error_code=error_code(error))

    def abandon(self) -> None:
        """Закрыть открытый этап брошенным (`REVIEW_RUN_STALE`): прогон отменили, и
        исхода у этапа нет; нет открытого — ничего."""
        if self._stage is not None:
            self.finish(error_code=ABANDONED)

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

    def _close(self, *, used_model: bool, error_code: str | None) -> None:
        self._current_stage()
        # Доля считается и для шага, который не записан: иначе токены этой
        # попытки достались бы следующей.
        share = self._share() if used_model else None
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
            if not self._write(closed, "исход", lambda: self._uow.review_run_steps.update(closed)):
                self._unwritten[closed.id] = closed
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
            # Прогон — атрибутом review_run_id из контекста логов: шаги пишутся
            # только во время обработки сообщения воркера.
            logger.exception("не удалось записать %s шага %s", what, step.id)
            return False
        return True

    def _current_stage(self) -> ReviewRunStage:
        if self._stage is None:
            raise RuntimeError(f"у прогона {self._run_id} нет открытого этапа")
        return self._stage

    def _share(self) -> int:
        total = self._meter.total.total_tokens
        # Счётчик только растёт, но доля шага отрицательной не бывает ни при каком писателе.
        share = max(total - self._recorded_tokens, 0)
        self._recorded_tokens = max(total, self._recorded_tokens)
        return share
