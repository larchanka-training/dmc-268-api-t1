"""Порт постановки задачи ревью в очередь.

Отдельно от `app/application/ports/repositories.py`: это не порт хранения,
а порт брокера сообщений. Держится вне `ports.__all__`, потому что этот
список используется тестом-гвардрейлом, заточенным под SQLAlchemy-адаптеры
портов хранения.
"""

from typing import Protocol

from app.domain.entities import ReviewJob


class JobQueue(Protocol):
    def enqueue(self, job: ReviewJob) -> None: ...
