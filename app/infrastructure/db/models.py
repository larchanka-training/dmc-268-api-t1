"""Таблицы конвейера ревью.

Enum-колонки строятся из доменных enum'ов, поэтому значение нельзя добавить в
одном месте и забыть в другом. Нативные enum-типы PostgreSQL, а не text плюс
CHECK: ограниченные наборы — часть контракта и должны держаться против любого
писателя, включая сессию psql.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    FAILURE_STAGES,
    TERMINAL_STATUSES,
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStage,
    ReviewRunStatus,
    ReviewRunStepStatus,
    TriggerSource,
    VerificationOutcome,
)
from app.infrastructure.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin

_TERMINAL_SQL = ", ".join(f"'{s.value}'" for s in sorted(TERMINAL_STATUSES))
_STAGES_SQL = ", ".join(f"'{s.value}'" for s in sorted(FAILURE_STAGES))


def _enum(python_enum: type, name: str) -> SAEnum:
    """Нативный enum PostgreSQL из единственного доменного определения."""
    return SAEnum(
        python_enum,
        name=name,
        native_enum=True,
        values_callable=lambda e: [member.value for member in e],
    )


class RepositoryRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_repositories_provider_id"),
    )

    provider: Mapped[Provider] = mapped_column(_enum(Provider, "provider"), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    auto_review_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Их никто не читает. Они нужны потому, что unit of work упорядочивает
    # INSERT'ы по relationship, а не по внешнему ключу: без них родитель и его
    # ребёнок, попавшие в один flush, уйдут в неверном порядке.
    # passive_deletes="all" не даёт ORM удалять детей или оставлять их
    # сиротами, так что решает RESTRICT.
    merge_requests: Mapped[list[MergeRequestRow]] = relationship(
        back_populates="repository", passive_deletes="all"
    )


class MergeRequestRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """Запрос на изменения. Покрывает и pull request в GitHub, и MR в GitLab."""

    __tablename__ = "merge_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_merge_requests_repo_number"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    source_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    target_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[MergeRequestState] = mapped_column(
        _enum(MergeRequestState, "merge_request_state"), nullable=False
    )

    repository: Mapped[RepositoryRow] = relationship(back_populates="merge_requests")
    review_runs: Mapped[list[ReviewRunRow]] = relationship(
        back_populates="merge_request", passive_deletes="all"
    )


class ReviewRunRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """Одна попытка отревьюить запрос на изменения на конкретном коммите.

    В тикете это называется ReviewJob. Имя зарезервировано за сообщением в
    очереди, чтобы долгоживущая строка и короткоживущее сообщение не носили
    одно имя.
    """

    __tablename__ = "review_runs"
    __table_args__ = (
        # Не больше одного незавершённого прогона на коммит. Именно это не даёт
        # повторно доставленному webhook'у запустить второе ревью, и поэтому же
        # брошенный прогон нужно подчищать: иначе он заблокирует свой коммит
        # навсегда.
        Index(
            "uq_review_runs_one_active_per_commit",
            "merge_request_id",
            "head_sha",
            unique=True,
            postgresql_where=text(f"status NOT IN ({_TERMINAL_SQL})"),
        ),
        # Этап сбоя есть только у упавшего прогона и только из числа этапов
        # обработки: правило `advance` держится против любого писателя.
        CheckConstraint(
            "failed_stage IS NULL"
            f" OR (status = 'failed' AND failed_stage IN ({_STAGES_SQL}))",
            name="ck_review_runs_failed_stage",
        ),
        CheckConstraint(
            "verifier_dropped_findings >= 0",
            name="ck_review_runs_verifier_dropped",
        ),
    )

    merge_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("merge_requests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    # Ревизия, относительно которой считался дифф этого прогона. Nullable:
    # прогон, записанный до того, как воркер научился её сообщать, её не знает.
    base_sha: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ReviewRunStatus] = mapped_column(
        _enum(ReviewRunStatus, "review_run_status"), nullable=False
    )
    trigger: Mapped[TriggerSource] = mapped_column(
        _enum(TriggerSource, "trigger_source"), nullable=False
    )
    last_progress_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # Тот же enum-тип, что у `status`: отдельный повторил бы список статусов.
    failed_stage: Mapped[ReviewRunStatus | None] = mapped_column(
        _enum(ReviewRunStatus, "review_run_status")
    )
    model: Mapped[str | None] = mapped_column(String(255))
    tokens_used: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    rejected_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Итог второго прохода модели; пуст, пока анализ не завершён, и у прогонов,
    # записанных до появления верификации.
    verification: Mapped[VerificationOutcome | None] = mapped_column(
        _enum(VerificationOutcome, "review_verification")
    )
    verifier_dropped_findings: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    merge_request: Mapped[MergeRequestRow] = relationship(back_populates="review_runs")


class ReviewRunStepRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """Шаг воркера в прогоне — «ход прогона» в кабинете.

    Повтор этапа — отдельная строка. Запросов и ответов модели здесь нет:
    они остаются в `context_payloads` и в логах, а шаг — только исход.
    """

    __tablename__ = "review_run_steps"
    __table_args__ = (
        # Длительности нет ровно у идущего шага, кода сбоя — у всех, кроме
        # упавшего: закрыть шаг наполовину не может ни один писатель.
        CheckConstraint(
            "(status = 'running') = (duration_seconds IS NULL)",
            name="ck_review_run_steps_duration",
        ),
        CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)",
            name="ck_review_run_steps_error_code",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_review_run_steps_duration_non_negative",
        ),
        CheckConstraint(
            "tokens_used IS NULL OR tokens_used >= 0",
            name="ck_review_run_steps_tokens_non_negative",
        ),
        # Шаги прогона читаются по порядку начала (`list_for_run`): индекс
        # отдаёт их уже отсортированными. Он же служит внешнему ключу.
        Index("ix_review_run_steps_run_order", "review_run_id", "started_at", "id"),
    )

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="RESTRICT"), nullable=False
    )
    stage: Mapped[ReviewRunStage] = mapped_column(
        _enum(ReviewRunStage, "review_run_stage"), nullable=False
    )
    status: Mapped[ReviewRunStepStatus] = mapped_column(
        _enum(ReviewRunStepStatus, "review_run_step_status"), nullable=False
    )
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    tokens_used: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(64))


class ContextPayloadRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """Что показали модели; хранится, чтобы прогон оставался воспроизводимым.

    В этой таблице дословно лежит чужой исходный код. Вычищать секреты нужно до
    вставки, в сборщике контекста; именно об этой чувствительной таблице и идёт
    речь в политике хранения.
    """

    __tablename__ = "context_payloads"
    __table_args__ = (
        UniqueConstraint("review_run_id", "chunk_index", name="uq_context_payloads_chunk"),
        Index("ix_context_payloads_digest", "content_sha256"),
    )

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tiers: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    file_paths: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class FindingRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """Замечание ревью, привязанное к строке, которую затронул дифф."""

    __tablename__ = "findings"
    __table_args__ = (
        # Схлопывание дублей — дело базы, чтобы ни один путь вставки не смог о
        # нём забыть. Домен тоже дедуплицирует: так вызывающий узнаёт, что было
        # отброшено, вместо того чтобы ловить ошибку целостности.
        #
        # Ключ — вся привязка целиком. Заполнен только номер строки на стороне
        # привязки, поэтому именно NULLS NOT DISTINCT заставляет ограничение
        # вообще срабатывать на старой стороне, где new_line всегда NULL.
        UniqueConstraint(
            "review_run_id",
            "file_path",
            "side",
            "old_line",
            "new_line",
            "category",
            name="uq_findings_anchor",
            postgresql_nulls_not_distinct=True,
        ),
        # Ссылка на правило заполнена целиком или пуста целиком: префикс
        # атрибуции строится из всех трёх частей.
        CheckConstraint(
            "(rule_name IS NULL AND rule_check IS NULL AND rule_check_text IS NULL)"
            " OR (rule_name IS NOT NULL AND rule_check IS NOT NULL"
            " AND rule_check_text IS NOT NULL AND rule_check >= 1)",
            name="ck_findings_rule_ref",
        ),
    )

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[DiffSide] = mapped_column(_enum(DiffSide, "diff_side"), nullable=False)
    old_line: Mapped[int | None] = mapped_column(Integer)
    new_line: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[FindingCategory] = mapped_column(
        _enum(FindingCategory, "finding_category"), nullable=False
    )
    severity: Mapped[FindingSeverity] = mapped_column(
        _enum(FindingSeverity, "finding_severity"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    # Пункт правила владельца, по которому выпущено замечание; текст пункта -
    # копия на момент прогона. Пусто целиком - замечание по стандартным критериям.
    rule_name: Mapped[str | None] = mapped_column(Text)
    rule_check: Mapped[int | None] = mapped_column(Integer)
    rule_check_text: Mapped[str | None] = mapped_column(Text)


class PublishedCommentRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """Комментарий, опубликованный обратно на хостинг."""

    __tablename__ = "published_comments"
    __table_args__ = (
        # Замечание публикуется не больше одного раза за прогон, поэтому повтор
        # не задвоит комментарий. У сводки замечания нет, и то же ограничение
        # распространяет на неё именно NULLS NOT DISTINCT: без него Postgres
        # считает каждый NULL finding_id уникальным, и повторная публикация
        # сохранит для прогона вторую сводку.
        UniqueConstraint(
            "review_run_id",
            "finding_id",
            name="uq_published_comments_finding",
            postgresql_nulls_not_distinct=True,
        ),
    )

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("findings.id", ondelete="RESTRICT")
    )
    provider_comment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[CommentKind] = mapped_column(_enum(CommentKind, "comment_kind"), nullable=False)
    published_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    thread_resolved: Mapped[bool] = mapped_column(default=False, server_default="false")
    in_reply_to_provider_comment_id: Mapped[str | None] = mapped_column(String(255))
    feedback_positive: Mapped[int] = mapped_column(default=0, server_default="0")
    feedback_negative: Mapped[int] = mapped_column(default=0, server_default="0")


class UserRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """Пользователь кабинета. Логин, имя и аватар обновляются при каждом входе."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_users_provider_user_id"),
    )

    provider: Mapped[Provider] = mapped_column(_enum(Provider, "provider"), nullable=False)
    # Строка: тип идентификатора у провайдеров разный.
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    login: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)

    # Нужна только для порядка INSERT'ов в одном flush, как у repositories.
    # Сессии удаляет база (CASCADE), а не ORM.
    sessions: Mapped[list[SessionRow]] = relationship(
        back_populates="user", passive_deletes="all"
    )


class SessionRow(Base, UuidPrimaryKeyMixin):
    """Сессия входа. Ни updated_at, ни продления: срок жизни задаётся при входе."""

    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_sessions_token_hash"),)

    # SHA-256 от значения cookie; самого значения в базе нет. Уникальный
    # индекс — путь поиска сессии на каждом запросе.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Удаление пользователя гасит его сессии — в отличие от RESTRICT конвейера,
    # сессия без пользователя ничего не значит.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[UserRow] = relationship(back_populates="sessions")
