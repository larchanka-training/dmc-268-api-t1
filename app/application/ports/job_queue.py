"""Порт постановки задачи ревью в очередь.

Отдельно от `app/application/ports/repositories.py`: это не порт хранения,
а порт брокера сообщений. Входит в `ports.__all__`, адаптер сверяет
`tests/test_container.py`.
"""

from typing import Protocol

from app.domain.entities import ReviewJob


class JobQueue(Protocol):
    def enqueue(self, job: ReviewJob) -> None: ...
