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
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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
    TERMINAL_STATUSES,
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.infrastructure.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin

_TERMINAL_SQL = ", ".join(f"'{s.value}'" for s in sorted(TERMINAL_STATUSES))


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
    model: Mapped[str | None] = mapped_column(String(255))
    tokens_used: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    rejected_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    merge_request: Mapped[MergeRequestRow] = relationship(back_populates="review_runs")


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
