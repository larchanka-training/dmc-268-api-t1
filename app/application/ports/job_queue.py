"""Очередь задач ревью.

Своё по сети — порт обязателен (`.agents/rules/backend.md`, категория
«своё по сети»): адаптер RabbitMQ и фейк в тестах. Структурный протокол:
адаптер этот модуль не импортирует, ниже нет ни pika, ни имён обменов.

`priority` — часть job'а, но не тела сообщения: как именно приоритет доезжает
до брокера, решает адаптер (`docs/SYSTEM_DESIGN.md` §4.2 — свойством AMQP).
"""

from typing import Protocol

from app.domain.entities import ReviewJob


class JobQueue(Protocol):
    def enqueue(self, job: ReviewJob) -> None:
        """Поставить задачу ревью; метаданные доставки — на совести адаптера."""
        ...
