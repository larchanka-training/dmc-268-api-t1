"""Порт записи исхода прогона: счётчик бизнес-метрик для алертов.

Счётчик `review_runs_total` — эксплуатационная телеметрия для команды, данные
продукта (статус и ход прогона) пользователь берёт из базы, и ни один ответ
API из метрик не строится (спека `observability`). Порт нужен ради границы
слоя: слой приложения не знает OpenTelemetry, адаптер живёт в
`app/infrastructure/telemetry.py`. Считаются прогоны, доведённые до
терминального статуса тем, кто этот переход сделал, — пайплайн
(`run_review`) или выметатель (`sweep_stale_runs`), — а не обработки
сообщений: повторная доставка терминального прогона и сообщение без прогона
не пишутся. Без адаптера вызывающий берёт `NO_RUN_OUTCOME` — записи нет,
поведение прогона от неё не зависит.
"""

from typing import Protocol


class RunOutcomeRecorder(Protocol):
    def run_finished(self, status: str) -> None:
        """Записать перевод прогона в терминальный статус (`completed`/`failed`)."""
        ...


class _NoRunOutcome:
    def run_finished(self, status: str) -> None:
        return None


NO_RUN_OUTCOME: RunOutcomeRecorder = _NoRunOutcome()
