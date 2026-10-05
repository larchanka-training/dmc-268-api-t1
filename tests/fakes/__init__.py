"""Двойники портов для тестов без базы, брокера и сети.

`unit_of_work` — транзакционный фейк хранилища; `webhooks` — двойники приёма
вебхуков (VCS, очередь, builder'ы). Реэкспорт в `__init__` держит старые
импорты `from ..fakes import ...` рабочими.
"""

from .container import FakeContainer
from .unit_of_work import FakeUnitOfWork
from .webhooks import (
    DIFF,
    INSTALLATION_ID,
    NOW,
    REGISTERED_PROVIDER_ID,
    REPO_FULL_NAME,
    FakeQueue,
    FakeVcs,
    a_repository,
    pr_metadata,
    webhook_payload,
)

# Имя, которое ждут тесты приёма: тот же транзакционный фейк.
FakeUow = FakeUnitOfWork

__all__ = [
    "DIFF",
    "INSTALLATION_ID",
    "NOW",
    "REGISTERED_PROVIDER_ID",
    "REPO_FULL_NAME",
    "FakeContainer",
    "FakeQueue",
    "FakeUnitOfWork",
    "FakeUow",
    "FakeVcs",
    "a_repository",
    "pr_metadata",
    "webhook_payload",
]
