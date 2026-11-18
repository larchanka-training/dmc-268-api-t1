"""Демо-режим (change `demo-mode`): кто демо-пользователь и что такое демо-репозиторий.

Признак демо — в ключах провайдера, без своей колонки: GitHub выдаёт числовые id, поэтому
`demo` у учётной записи и `demo-<n>` у репозитория настоящему GitHub не встречаются. Отсюда
же видимость: настоящему пользователю GitHub такой репозиторий не отдаст, и демо-данные
ему не видны без отдельной проверки.
"""

from uuid import UUID

from app.domain.entities import (
    InstallationRepository,
    Repository,
    RepositoryPermissions,
    User,
)
from app.domain.enums import Provider

DEMO_PROVIDER_USER_ID = "demo"
DEMO_REPOSITORY_PREFIX = "demo-"
# Сессии у демо-пользователя нет, а `SignedIn` несёт id сессии: постоянный, в базе такого нет.
DEMO_SESSION_ID = UUID("00000000-0000-7000-8000-0000000000de")

# Демо — только чтение: ни подключить, ни настроить демо-репозиторий нельзя.
_READ_ONLY = RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)


def is_demo_user(user: User) -> bool:
    """Пользователь — демо-учётная запись, записанная скриптом начальных данных."""
    return user.provider is Provider.GITHUB and user.provider_user_id == DEMO_PROVIDER_USER_ID


def is_demo_repository(repository: Repository) -> bool:
    """Репозиторий — демо: его записал скрипт начальных данных, на GitHub его нет."""
    return repository.provider is Provider.GITHUB and repository.provider_id.startswith(DEMO_REPOSITORY_PREFIX)


def demo_installation_repository(repository: Repository) -> InstallationRepository:
    """Демо-репозиторий в том виде, в каком видимые репозитории отдаёт GitHub, — только чтение."""
    return InstallationRepository(
        provider_id=repository.provider_id,
        full_name=repository.full_name,
        default_branch=repository.default_branch,
        permissions=_READ_ONLY,
    )
