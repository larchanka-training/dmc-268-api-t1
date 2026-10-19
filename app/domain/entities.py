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
    DefinitionKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    PromptRole,
    Provider,
    ReviewRunStatus,
    RulesStatus,
    TriggerSource,
    VerificationOutcome,
    VerifierVerdict,
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
    # Этап, на котором прогон упал. Заполняет только `advance` при переходе в
    # `failed`; у прогона, упавшего из `queued`, этапа нет.
    failed_stage: ReviewRunStatus | None = None
    # Итог второго прохода модели; пуст, пока анализ не завершён.
    verification: VerificationOutcome | None = None
    verifier_dropped_findings: int = 0


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
class LlmRuleRef:
    """Ссылка модели на пункт правила - ещё не сверенная с показанными правилами."""

    name: str
    check: int


@dataclass(frozen=True, slots=True)
class LlmFinding:
    """Находка, как её вернула модель, - ещё без id, прогона и меток времени.

    Доменная запись, а не часть порта: её разбирает доменный отсев
    (`screen_findings`), который и превращает её в `Finding`.
    """

    anchor: DiffAnchor
    category: FindingCategory
    severity: FindingSeverity
    message: str
    suggestion: str | None = None
    confidence: float | None = None
    rule: LlmRuleRef | None = None


@dataclass(frozen=True, slots=True)
class RuleRef:
    """Пункт правила владельца, по которому выпущено замечание.

    Создаётся только отсевом (`screen_findings`) из ссылки модели, которую он
    сверил с правилами, показанными модели. Текст пункта - копия на момент
    прогона: правила в целевой ветке потом меняются, а замечание должно
    показывать пункт, по которому его выпустили.
    """

    name: str
    check: int
    check_text: str


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
    # Пусто - замечание по стандартным критериям ревью.
    rule: RuleRef | None = None


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
    thread_resolved: bool = False
    in_reply_to_provider_comment_id: str | None = None
    feedback_positive: int = 0
    feedback_negative: int = 0


@dataclass(frozen=True, slots=True)
class OpenReviewThread:
    """Незакрытый инлайн-тред на хостинге, оставшийся с прошлого прогона."""

    finding: Finding
    root_provider_comment_id: str
    root_published_comment_id: UUID


@dataclass(frozen=True, slots=True)
class ReviewJob:
    """Сообщение очереди, которое просит проверить один коммит.

    Не хранится: живёт ровно между `JobQueue.enqueue` и обработкой воркером.
    `id` — идентификатор сообщения, новый на каждую постановку (§4.2
    SYSTEM_DESIGN.md); прогон указывает `review_run_id`. Так две постановки
    одного прогона различимы в DLQ и в логах. `installation_id` — инсталляция
    провайдера для повторного получения диффа воркером (разбор диффа — у
    воркера, дифф в сообщении не возится).
    """

    id: UUID
    review_run_id: UUID
    event_type: str
    action: str
    installation_id: int
    repository_provider_id: str
    repository_full_name: str
    pull_request_number: int
    head_sha: str
    base_sha: str


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
    # Заголовок `@@` и тело hunk'а как в диффе: единица, которой сборка
    # контекста отмеряет бюджет и которую показывает модели.
    text: str = ""


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """Один файл диффа: пути, бинарность и разобранные hunk'и.

    `old_path` заполнен только у переименования. Бинарные файлы на hunk'и не
    разбираются — ревьюить там нечего, их остаётся только отфильтровать.
    `is_deleted` — у файла нет новой стороны: AST-окружение строить не из чего.
    """

    file_path: str
    old_path: str | None = None
    is_binary: bool = False
    is_deleted: bool = False
    hunks: tuple[Hunk, ...] = ()


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
class RepoRule:
    """Правило владельца репозитория из `.review/rules.md`, принятое разбором.

    Четыре части из спеки `ai-review-prompting`: имя, паттерны области
    действия, паттерны исключения, проверки. Исключения необязательны (пустой
    кортеж — не исключено ничего); без имени, паттерна или проверок правило
    не принимается — см. `parse_rules_markdown`.
    """

    name: str
    patterns: tuple[str, ...]
    excludes: tuple[str, ...]
    checks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RejectedRule:
    """Раздел `rules.md`, не ставший правилом, и чего ему не хватило."""

    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class RepoRules:
    """Итог загрузки правил для прогона: статус и то, что удалось разобрать.

    При любом статусе, кроме `LOADED`, оба кортежа пусты — дефолтный фоллбэк.
    """

    status: RulesStatus
    rules: tuple[RepoRule, ...] = ()
    rejected: tuple[RejectedRule, ...] = ()


@dataclass(frozen=True, slots=True)
class ShownRule:
    """Правило, которое вошло в пакет контекста, - то, что видела модель.

    Имя и проверки - в том виде, в каком они в пакете (обезврежены
    `sanitize`): модель копирует имя оттуда, а текст пункта уходит в
    опубликованный комментарий. Файлы - сырые пути, как у hunk'ов: с ними
    сверяется путь замечания. Ссылка на правило, которого здесь нет, -
    ссылка на то, чего модель не видела.
    """

    name: str
    files: tuple[str, ...]
    checks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Definition:
    """Определение в файле: класс, интерфейс, функция или метод.

    `signature` — заголовок до тела в одну строку (с базовыми классами,
    `extends`, ресивером Go). `parent` — имя охватывающего класса у метода.
    Строки — с единицы, как в диффе; `end_line` включительно.
    """

    kind: DefinitionKind
    name: str
    signature: str
    line: int
    end_line: int
    parent: str | None = None


@dataclass(frozen=True, slots=True)
class FileOutline:
    """Контур файла, как его отдаёт парсер: определения и импорты по порядку."""

    definitions: tuple[Definition, ...] = ()
    imports: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PromptMessage:
    """Одно сообщение запроса к модели.

    Собирается только сборщиком (`app/domain/prompt.py`); адаптер модели
    передаёт сообщения как есть.
    """

    role: PromptRole
    content: str


@dataclass(frozen=True, slots=True)
class Verdict:
    """Решение верификатора по замечанию и его причина (для лога прогона)."""

    verdict: VerifierVerdict
    reason: str


@dataclass(frozen=True, slots=True)
class User:
    """Пользователь кабинета. Ключ — учётная запись у провайдера входа."""

    id: UUID
    provider: Provider
    provider_user_id: str
    login: str
    name: str | None
    avatar_url: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Session:
    """Сессия входа. Хранится хэш значения cookie, а не само значение."""

    id: UUID
    token_hash: bytes
    user_id: UUID
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthProfile:
    """Профиль, который провайдер вернул при входе. Транзиентный: токена в нём нет."""

    provider_user_id: str
    login: str
    name: str | None
    avatar_url: str | None
