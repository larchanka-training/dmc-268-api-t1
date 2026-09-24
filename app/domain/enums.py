"""Ограниченные наборы значений, на которые опираются и схема, и пайплайн.

Определены здесь один раз; модели SQLAlchemy строят из них нативные enum'ы
PostgreSQL, поэтому значение нельзя добавить в одном месте и забыть в
другом.
"""

from enum import StrEnum


class Provider(StrEnum):
    """Хостинг системы контроля версий, где живёт репозиторий."""

    GITHUB = "github"
    GITLAB = "gitlab"


class TriggerSource(StrEnum):
    """Из-за чего был создан прогон ревью."""

    WEBHOOK = "webhook"
    MANUAL = "manual"
    MENTION = "mention"


class MergeRequestState(StrEnum):
    """Состояние запроса на изменения на хостинге, сведённое к общему для обоих.

    `locked` у GitLab и closed с `merged_at` у GitHub адаптер провайдера
    переводит в эти значения ещё до того, как они попадут в хранилище.
    """

    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class ReviewRunStatus(StrEnum):
    """Жизненный цикл одной попытки отревьюить коммит."""

    QUEUED = "queued"
    BUILDING_CONTEXT = "building_context"
    ANALYSING = "analysing"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FindingCategory(StrEnum):
    """Оси ревью, к которым может относиться замечание."""

    SECURITY = "security"
    CORRECTNESS = "correctness"
    PERFORMANCE = "performance"
    READABILITY = "readability"


class FindingSeverity(StrEnum):
    """Насколько замечание важно."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CommentKind(StrEnum):
    """Форма комментария, публикуемого обратно на хостинг."""

    SUMMARY = "summary"
    INLINE = "inline"


class DiffSide(StrEnum):
    """К какой стороне диффа относится координата строки."""

    OLD = "old"
    NEW = "new"


TERMINAL_STATUSES: frozenset[ReviewRunStatus] = frozenset(
    {
        ReviewRunStatus.COMPLETED,
        ReviewRunStatus.FAILED,
        ReviewRunStatus.CANCELLED,
    }
)
