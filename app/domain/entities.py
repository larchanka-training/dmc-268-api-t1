"""Доменные записи: хранимые и транзиентные структуры запроса.

Одни записи живут дольше запроса и попадают в базу (репозиторий, запрос на
изменение, прогон ревью, находка), другие транзиентны — hunk диффа, событие
вебхука, разобранный файл диффа. Все они — замороженные dataclass'ы без
единого фреймворка: собираются из литералов, сравниваются по значению и
тестируются без базы. Время и идентификаторы приходят аргументами, а не
читаются из часов или генератора, поэтому тест проверяет точный timestamp,
а не диапазон.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)


@dataclass(frozen=True, slots=True)
class Repository:
    """Репозиторий, который сервису разрешено ревьюить."""

    id: UUID
    provider: Provider
    provider_id: str
    full_name: str
    default_branch: str
    auto_review_enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MergeRequest:
    """Запрос на изменения на хостинге. Пул-реквест GitHub тоже сюда."""

    id: UUID
    repository_id: UUID
    number: int
    title: str
    description: str
    author: str
    source_branch: str
    target_branch: str
    head_sha: str
    state: MergeRequestState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewRun:
    """Одна попытка отревьюить запрос на изменения на конкретном коммите.

    Хранится как `review_runs`. В тикете это ReviewJob; имя зарезервировано за
    сообщением в очереди, чтобы долгоживущая запись и короткоживущее сообщение
    никогда не назывались одинаково.
    """

    id: UUID
    merge_request_id: UUID
    head_sha: str
    status: ReviewRunStatus
    trigger: TriggerSource
    last_progress_at: datetime
    created_at: datetime
    updated_at: datetime
    failure_reason: str | None = None
    base_sha: str | None = None
    model: str | None = None
    tokens_used: int | None = None
    duration_seconds: float | None = None
    rejected_findings: int = 0


@dataclass(frozen=True, slots=True)
class ContextPayload:
    """Один кусок материала, отправленного модели для прогона."""

    id: UUID
    review_run_id: UUID
    chunk_index: int
    tiers: tuple[str, ...]
    file_paths: tuple[str, ...]
    token_count: int
    content_sha256: str
    body: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DiffAnchor:
    """Куда внутри диффа указывает замечание.

    Создаётся через `validate_anchor`: именно он доказывает, что координаты
    попадают внутрь изменения, а не в произвольный номер строки.
    """

    file_path: str
    side: DiffSide
    old_line: int | None = None
    new_line: int | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    """То, что заметило ревью, привязанное к строке, затронутой диффом."""

    id: UUID
    review_run_id: UUID
    anchor: DiffAnchor
    category: FindingCategory
    severity: FindingSeverity
    message: str
    created_at: datetime
    updated_at: datetime
    suggestion: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class PublishedComment:
    """Комментарий, который сервис опубликовал обратно на хостинг."""

    id: UUID
    review_run_id: UUID
    provider_comment_id: str
    kind: CommentKind
    published_at: datetime
    created_at: datetime
    updated_at: datetime
    finding_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Hunk:
    """Непрерывный диапазон, затронутый диффом, как его выдаёт парсер."""

    file_path: str
    old_start: int = 0
    old_count: int = 0
    new_start: int = 0
    new_count: int = 0
    changed_new_lines: frozenset[int] = field(default_factory=frozenset)
    changed_old_lines: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """Событие вебхука, запускающее ревью.

    Базового коммита в событии нет: `base.sha` из payload'а может быть
    устаревшим, поэтому базовый коммит всегда берётся из свежих метаданных
    PR через `fetch_pr_metadata`. Провайдер один — GitHub, но поля уже
    нейтральны к нему. `action` — исходное действие GitHub (`opened`,
    `synchronize`): только эти два проходят через `extract_github_event`,
    и оно же переезжает в `ReviewJob.action` без преобразований.
    """

    action: str
    installation_id: int
    repo_full_name: str
    repo_provider_id: str
    pr_number: int
    head_sha: str
    source_branch: str
    target_branch: str
    title: str
    author: str


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """Один файл диффа: пути, бинарность и разобранные hunk'и.

    `old_path` заполнен только у переименования. Бинарные файлы на hunk'и не
    разбираются — ревьюить там нечего, их остаётся только отфильтровать.
    """

    file_path: str
    old_path: str | None = None
    is_binary: bool = False
    hunks: tuple[Hunk, ...] = ()


@dataclass(frozen=True, slots=True)
class PRMetadata:
    """Свежие метаданные запроса на изменения, достанные через VCS-шлюз.

    Транзиентна, как и разобранный дифф: воркер достаёт её заново. Базовый
    коммит — только отсюда, а не из payload'а вебхука, где он успевает
    устареть. Состояние уже переведено адаптером в общий enum; «открыт»,
    «закрыт» и «слит» — всё, что знает о нём домен.
    """

    number: int
    head_sha: str
    base_sha: str
    title: str
    author: str
    source_branch: str
    target_branch: str
    state: MergeRequestState


@dataclass(frozen=True, slots=True)
class ReviewJob:
    """Сообщение очереди: команда воркеру отревьюить прогон.

    Тело сериализуется строго по `docs/SYSTEM_DESIGN.md` §4.2. Отклонение от
    списка полей плана (`tasks/plan.md`, Task 3.1): добавлены `action` и
    `repository_provider_id` — §4.2 требует нести в теле `action` и числовой
    `repository.id`, без них сообщение не собрать. `priority` в тело не
    входит: это метаданное доставки, адаптер передаёт его свойством AMQP.
    `job_id` — UUIDv7 из `app.domain.ids.new_id()`, задаётся при создании
    задачи доменом, а не адаптером и не базой: у сообщения есть
    идентификатор ещё до брокера.
    """

    job_id: UUID
    review_run_id: UUID
    repository_full_name: str
    repository_provider_id: int
    pr_number: int
    head_sha: str
    base_sha: str | None
    action: str
    priority: int
