"""Аналитика кабинета: что значат период и выборка, без SQL.

Учитываются только прогоны `completed`, сделавшие ревью: прогон без денег (`coverage_reason =
balance`) модель не вызывал, его нулевая длительность занизила бы среднюю. Период — по
`created_at` прогона, начало включительно, конец исключительно; день находки — UTC-день её
прогона, поэтому замечания и длительности считаются по одним и тем же прогонам. Замечания —
только новые: повтор открытого или закрытого человеком треда (`covered_by_finding_id`) не считается.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from app.domain.enums import FindingCategory, FindingSeverity

DEFAULT_PERIOD = timedelta(days=30)
MAX_PERIOD = timedelta(days=180)


@dataclass(frozen=True, slots=True)
class AnalyticsFilter:
    """Выборка: `repository_ids` — уже пересечение видимых и запрошенных, пустой — пустой ответ."""

    repository_ids: frozenset[UUID]
    created_from: datetime
    created_to: datetime


@dataclass(frozen=True, slots=True)
class FindingsDayCount:
    date: date
    category: FindingCategory
    severity: FindingSeverity
    count: int


@dataclass(frozen=True, slots=True)
class RunDurations:
    """Учтённые прогоны и их длительность; без прогонов средняя и p95 — `None`."""

    count: int
    average_duration_seconds: float | None
    p95_duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class RepositoryAnalytics:
    repository_id: UUID
    full_name: str
    runs_count: int
    # Строка есть только при runs_count ≥ 1, а у завершённого прогона длительность есть всегда
    # (CHECK `(status = 'running') = (duration_seconds IS NULL)` на review_runs).
    average_duration_seconds: float
    p95_duration_seconds: float
    findings_count: int


@dataclass(frozen=True, slots=True)
class Analytics:
    findings_by_day: tuple[FindingsDayCount, ...]
    runs: RunDurations
    by_repository: tuple[RepositoryAnalytics, ...]


EMPTY_ANALYTICS = Analytics(findings_by_day=(), runs=RunDurations(0, None, None), by_repository=())
