"""Поддельный `RunOutcomeRecorder`: запоминает статусы переводов прогона.

Утиной реализации импорт порта не нужен: совместимость с ним проверяют
вызовом, как у любого подменяемого порта.
"""


class RecordingRunOutcome:
    """Кладёт статусы в список: тест сверяет их порядок и кратность."""

    def __init__(self) -> None:
        self.statuses: list[str] = []

    def run_finished(self, status: str) -> None:
        self.statuses.append(status)
