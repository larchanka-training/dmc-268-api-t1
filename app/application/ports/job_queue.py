"""Порт постановки задачи в очередь: ревью запроса на изменение или симуляции песочницы.

Отдельно от `app/application/ports/repositories.py`: это не порт хранения,
а порт брокера сообщений. Входит в `ports.__all__`, адаптер сверяет
`tests/test_container.py`. Вид задачи и её приоритет адаптер выводит из типа
сообщения: симуляция встаёт за ждущими ревью.
"""

from typing import Protocol

from app.domain.entities import ReviewJob, SimulationJob


class JobQueue(Protocol):
    def enqueue(self, job: ReviewJob | SimulationJob) -> None: ...
