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


class ReviewRunStage(StrEnum):
    """Этап работы воркера над прогоном — то, что показывает «ход прогона».

    Мельче статуса прогона: в `building_context` воркер сначала получает дифф,
    потом собирает контекст, в `analysing` — ревью, потом верификация.
    """

    FETCHING_DIFF = "fetching_diff"
    BUILDING_CONTEXT = "building_context"
    ANALYSING = "analysing"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"


class ReviewRunStepStatus(StrEnum):
    """Состояние одного шага прогона."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


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
    THREAD_REPLY = "thread_reply"


class ThreadFollowUpStatus(StrEnum):
    """Как модель оценивает открытый тред на повторном прогоне."""

    FIXED = "fixed"
    STILL_RELEVANT = "still_relevant"
    ACCEPTED_RISK = "accepted_risk"
    SUPERSEDED = "superseded"


class DiffSide(StrEnum):
    """К какой стороне диффа относится координата строки."""

    OLD = "old"
    NEW = "new"


class RulesStatus(StrEnum):
    """Чем закончилась загрузка `.review/rules.md` для прогона.

    Всё, кроме `LOADED`, — дефолтный фоллбэк: ревью идёт по стандартным
    критериям, прогон не падает.
    """

    LOADED = "loaded"
    ABSENT = "absent"
    TOO_LARGE = "too_large"
    UNAVAILABLE = "unavailable"


class VerificationOutcome(StrEnum):
    """Чем закончился второй проход модели (Reflection Pass) для прогона.

    `unavailable` - fail-open: верификатор упал или ответил не по схеме, и
    опубликованы замечания, прошедшие только детерминированный отсев.
    """

    VERIFIED = "verified"
    NOT_REQUIRED = "not_required"
    UNAVAILABLE = "unavailable"


class VerifierVerdict(StrEnum):
    """Решение верификатора по одному замечанию первого прохода."""

    KEEP = "keep"
    DROP = "drop"


class PromptRole(StrEnum):
    """Роль сообщения в запросе к модели."""

    SYSTEM = "system"
    USER = "user"


class Language(StrEnum):
    """Язык файла, для которого есть грамматика AST-окружения."""

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    JAVASCRIPT = "javascript"
    GO = "go"


class DefinitionKind(StrEnum):
    """Вид определения в контуре файла."""

    CLASS = "class"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"


# Этапы обработки — то, что может стоять в `failed_stage`. `queued` сюда не
# входит: прогон, упавший до начала работы, ни на каком этапе не был.
FAILURE_STAGES: frozenset[ReviewRunStatus] = frozenset(
    {
        ReviewRunStatus.BUILDING_CONTEXT,
        ReviewRunStatus.ANALYSING,
        ReviewRunStatus.PUBLISHING,
    }
)

TERMINAL_STATUSES: frozenset[ReviewRunStatus] = frozenset(
    {
        ReviewRunStatus.COMPLETED,
        ReviewRunStatus.FAILED,
        ReviewRunStatus.CANCELLED,
    }
)
