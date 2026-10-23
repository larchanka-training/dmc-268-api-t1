"""Список прогонов: что значат фильтры GET /reviews, без SQL.

Параметры запроса приходят уже проверенными; здесь они сводятся к условиям
выборки, которые адаптер переводит в запрос как есть: набор статусов вместо пары
`status` + `active`, номер запроса на изменение из `q` отдельно от подстроки.
"""

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities import ReviewRunListEntry
from app.domain.enums import TERMINAL_STATUSES, ReviewRunStatus

ACTIVE_STATUSES: frozenset[ReviewRunStatus] = frozenset(ReviewRunStatus) - TERMINAL_STATUSES

# Номер в `q` — `42` или `#42`; больше int4 номера не бывает (колонка `number`).
_NUMBER = re.compile(r"#?(\d{1,10})")
_MAX_NUMBER = 2_147_483_647


@dataclass(frozen=True, slots=True)
class RunListFilter:
    """Условия выборки, складываются через «и».

    `repository_ids` — уже пересечение видимых пользователю и запрошенных: пустой
    набор — пустой список. `statuses = None` — любой статус, пустой набор — ни
    одного. `text` — подстрока без учёта регистра в заголовке, авторе или исходной
    ветке; `number`, если `q` похож на номер, — ещё и точное совпадение номера.
    """

    repository_ids: frozenset[UUID]
    statuses: frozenset[ReviewRunStatus] | None = None
    author: str | None = None
    text: str | None = None
    number: int | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class RunListPage:
    """Страница списка и число прогонов под фильтрами без учёта limit и offset."""

    entries: list[ReviewRunListEntry]
    total: int


def run_list_filter(
    *,
    visible: Collection[UUID],
    repository_ids: Collection[UUID] | None,
    statuses: Collection[ReviewRunStatus] | None,
    active: bool | None,
    author: str | None,
    q: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> RunListFilter:
    """Собрать фильтр из параметров запроса; пустые `q` и `author` — без фильтра."""
    allowed = frozenset(visible)
    if repository_ids:
        allowed &= frozenset(repository_ids)
    picked = frozenset(statuses) if statuses else None
    if active is not None:
        side = ACTIVE_STATUSES if active else TERMINAL_STATUSES
        picked = side if picked is None else picked & side
    text = q or None
    number = None
    if text is not None and (match := _NUMBER.fullmatch(text)) and int(match[1]) <= _MAX_NUMBER:
        number = int(match[1])
    return RunListFilter(
        repository_ids=allowed,
        statuses=picked,
        author=author or None,
        text=text,
        number=number,
        created_from=created_from,
        created_to=created_to,
    )
