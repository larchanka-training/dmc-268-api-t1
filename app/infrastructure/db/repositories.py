"""Реализации портов хранения на SQLAlchemy.

Тонкие намеренно: каждый метод переводит между строкой таблицы и доменной
сущностью и больше ничего не делает. Некоторые перед записью применяют
чистую функцию — это единственные места, где появляется правило. Они отмечены.
"""

import datetime as dt
from collections.abc import Collection, Sequence
from typing import Any
from uuid import UUID

from psycopg.errors import LockNotAvailable
from sqlalchemy import (
    CTE,
    ColumnElement,
    Row,
    delete,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import InstrumentedAttribute, Session, aliased
from sqlalchemy.orm.attributes import flag_modified

from app.domain.entities import (
    BotThread,
    ContextPayload,
    Finding,
    MergeRequest,
    OpenReviewThread,
    ProviderToken,
    PublishedComment,
    Repository,
    ReviewRun,
    ReviewRunListEntry,
    ReviewRunStep,
    Secret,
    Simulation,
    User,
)
from app.domain.entities import Session as UserSession
from app.domain.enums import (
    REVIEWED_STATUSES,
    TERMINAL_STATUSES,
    CommentKind,
    Provider,
    ReviewRunStatus,
    SimulationStatus,
    TriggerSource,
)
from app.domain.lifecycle import next_status
from app.domain.provider_token import ProviderTokenBusy, ProviderTokenUnavailable
from app.domain.run_feed import FeedCursor
from app.domain.run_list import RunListFilter, RunListPage
from app.domain.secret_redaction import redact_secrets
from app.domain.simulation import (
    SIMULATION_STALE,
    SIMULATION_TERMINAL_STATUSES,
    next_simulation_status,
)
from app.infrastructure.db import mappers as m
from app.infrastructure.db.models import (
    ContextPayloadRow,
    FindingRow,
    MergeRequestRow,
    ProviderTokenRow,
    PublishedCommentRow,
    RepositoryRow,
    ReviewRunRow,
    ReviewRunStepRow,
    SessionRow,
    SimulationRow,
    UserRow,
)
from app.infrastructure.db.run_changes import REVIEW_RUNS_CHANNEL
from app.infrastructure.db.token_cipher import TokenCipher


class SqlAlchemyRepositoryRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, repository_id: UUID) -> Repository | None:
        row = self._session.get(RepositoryRow, repository_id)
        return m.repository_to_domain(row) if row else None

    def find_by_provider(self, provider: Provider, provider_id: str) -> Repository | None:
        row = self._session.scalars(
            select(RepositoryRow).where(
                RepositoryRow.provider == provider,
                RepositoryRow.provider_id == provider_id,
            )
        ).one_or_none()
        return m.repository_to_domain(row) if row else None

    def list_by_provider_ids(self, provider: Provider, provider_ids: Collection[str]) -> list[Repository]:
        if not provider_ids:
            return []
        rows = self._session.scalars(
            select(RepositoryRow)
            .where(RepositoryRow.provider == provider, RepositoryRow.provider_id.in_(provider_ids))
            .order_by(RepositoryRow.id)
        ).all()
        return [m.repository_to_domain(row) for row in rows]

    def add(self, repository: Repository) -> bool:
        # `ON CONFLICT DO NOTHING`, как у пользователей: параллельное
        # подключение того же репозитория ждёт коммита первого и получает
        # 0 строк, а не `IntegrityError`. Время записи — то, что пришло в
        # сущности: ответ подключения совпадает с записью.
        with self._session.no_autoflush:
            inserted = self._session.execute(
                pg_insert(RepositoryRow)
                .values(
                    id=repository.id,
                    provider=repository.provider,
                    provider_id=repository.provider_id,
                    full_name=repository.full_name,
                    default_branch=repository.default_branch,
                    auto_review_enabled=repository.auto_review_enabled,
                    created_at=repository.created_at,
                    updated_at=repository.updated_at,
                    connected_at=repository.connected_at,
                )
                .on_conflict_do_nothing(constraint="uq_repositories_provider_id")
                .returning(RepositoryRow.id)
            ).first()
        return inserted is not None

    def reconnect(self, repository: Repository) -> Repository | None:
        # Условие `connected_at IS NULL` — в самом UPDATE: второе параллельное
        # включение ждёт блокировку строки, перечитывает её и уже не совпадает.
        row = self._session.scalars(
            update(RepositoryRow)
            .where(
                RepositoryRow.provider == repository.provider,
                RepositoryRow.provider_id == repository.provider_id,
                RepositoryRow.connected_at.is_(None),
            )
            .values(
                connected_at=repository.connected_at,
                full_name=repository.full_name,
                default_branch=repository.default_branch,
                updated_at=repository.updated_at,
            )
            .returning(RepositoryRow)
        ).one_or_none()
        return m.repository_to_domain(row) if row else None

    def disconnect(self, repository_id: UUID, now: dt.datetime) -> None:
        self._session.execute(
            update(RepositoryRow)
            .where(RepositoryRow.id == repository_id)
            .values(connected_at=None, updated_at=now)
        )

    def update(self, repository: Repository) -> None:
        row = self._session.get(RepositoryRow, repository.id)
        if row is None:
            raise LookupError(f"repository {repository.id} is not stored")
        row.full_name = repository.full_name
        row.default_branch = repository.default_branch
        row.auto_review_enabled = repository.auto_review_enabled


class SqlAlchemyMergeRequestRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, merge_request_id: UUID) -> MergeRequest | None:
        row = self._session.get(MergeRequestRow, merge_request_id)
        return m.merge_request_to_domain(row) if row else None

    def find_by_number(self, repository_id: UUID, number: int) -> MergeRequest | None:
        row = self._session.scalars(
            select(MergeRequestRow).where(
                MergeRequestRow.repository_id == repository_id,
                MergeRequestRow.number == number,
            )
        ).one_or_none()
        return m.merge_request_to_domain(row) if row else None

    def list_for_repository(
        self, repository_id: UUID, limit: int, offset: int
    ) -> list[MergeRequest]:
        rows = self._session.scalars(
            select(MergeRequestRow)
            .where(MergeRequestRow.repository_id == repository_id)
            .order_by(MergeRequestRow.number)
            .limit(limit)
            .offset(offset)
        ).all()
        return [m.merge_request_to_domain(row) for row in rows]

    def add(self, merge_request: MergeRequest) -> None:
        self._session.add(
            MergeRequestRow(
                id=merge_request.id,
                repository_id=merge_request.repository_id,
                number=merge_request.number,
                title=merge_request.title,
                description=merge_request.description,
                author=merge_request.author,
                source_branch=merge_request.source_branch,
                target_branch=merge_request.target_branch,
                head_sha=merge_request.head_sha,
                state=merge_request.state,
            )
        )

    def update(self, merge_request: MergeRequest) -> None:
        row = self._session.get(MergeRequestRow, merge_request.id)
        if row is None:
            raise LookupError(f"merge request {merge_request.id} is not stored")
        row.title = merge_request.title
        row.description = merge_request.description
        row.source_branch = merge_request.source_branch
        row.target_branch = merge_request.target_branch
        row.head_sha = merge_request.head_sha
        row.state = merge_request.state


def _run_list_conditions(run_filter: RunListFilter) -> list[ColumnElement[bool]]:
    """Условия фильтра списка прогонов; запрос уже соединён с `merge_requests`."""
    conditions: list[ColumnElement[bool]] = [MergeRequestRow.repository_id.in_(run_filter.repository_ids)]
    if run_filter.statuses is not None:
        conditions.append(ReviewRunRow.status.in_(run_filter.statuses))
    if run_filter.author is not None:
        conditions.append(MergeRequestRow.author == run_filter.author)
    if run_filter.text is not None:
        text_columns = (MergeRequestRow.title, MergeRequestRow.author, MergeRequestRow.source_branch)
        matches = [column.icontains(run_filter.text, autoescape=True) for column in text_columns]
        if run_filter.number is not None:
            matches.append(MergeRequestRow.number == run_filter.number)
        conditions.append(or_(*matches))
    if run_filter.created_from is not None:
        conditions.append(ReviewRunRow.created_at >= run_filter.created_from)
    if run_filter.created_to is not None:
        conditions.append(ReviewRunRow.created_at < run_filter.created_to)
    return conditions


class SqlAlchemyReviewRunRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, run_id: UUID) -> ReviewRun | None:
        row = self._session.get(ReviewRunRow, run_id)
        return m.review_run_to_domain(row) if row else None

    def find_active(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        # `first`, а не `one_or_none`: в окне гонки индекс ещё не разрешал
        # коллизию, и две нетерминальные строки на этот коммит — возможный
        # промежуточный ответ; MultipleResultsFound здесь означал бы 500.
        row = self._session.scalars(
            select(ReviewRunRow)
            .where(
                ReviewRunRow.merge_request_id == merge_request_id,
                ReviewRunRow.head_sha == head_sha,
                ReviewRunRow.status.not_in(TERMINAL_STATUSES),
            )
            .order_by(ReviewRunRow.created_at)
        ).first()
        return m.review_run_to_domain(row) if row else None

    def find_reviewed(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        # Прогонов на один merge_request — единицы: индекс по merge_request_id
        # сужает выборку, частичный уникальный индекс терминальные строки не видит.
        row = self._session.scalars(
            select(ReviewRunRow)
            .where(
                ReviewRunRow.merge_request_id == merge_request_id,
                ReviewRunRow.head_sha == head_sha,
                ReviewRunRow.trigger == TriggerSource.WEBHOOK,
                ReviewRunRow.status.in_(REVIEWED_STATUSES),
            )
            .order_by(ReviewRunRow.created_at.desc(), ReviewRunRow.id.desc())
            .limit(1)
        ).first()
        return m.review_run_to_domain(row) if row else None

    def find_latest(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        row = self._session.scalars(
            select(ReviewRunRow)
            .where(
                ReviewRunRow.merge_request_id == merge_request_id,
                ReviewRunRow.head_sha == head_sha,
            )
            .order_by(ReviewRunRow.created_at.desc(), ReviewRunRow.id.desc())
            .limit(1)
        ).first()
        return m.review_run_to_domain(row) if row else None

    def list_unfinished(self) -> list[ReviewRun]:
        rows = self._session.scalars(
            select(ReviewRunRow).where(ReviewRunRow.status.not_in(TERMINAL_STATUSES))
        ).all()
        return [m.review_run_to_domain(row) for row in rows]

    def list_unpublished_failures(self, merge_request_id: UUID) -> list[ReviewRun]:
        published = select(PublishedCommentRow.id).where(
            PublishedCommentRow.review_run_id == ReviewRunRow.id
        )
        published_run = aliased(ReviewRunRow)
        last_published_at = (
            select(func.max(PublishedCommentRow.published_at))
            .join(published_run, PublishedCommentRow.review_run_id == published_run.id)
            .where(published_run.merge_request_id == merge_request_id)
            .scalar_subquery()
        )
        rows = self._session.scalars(
            select(ReviewRunRow)
            .where(
                ReviewRunRow.merge_request_id == merge_request_id,
                ReviewRunRow.status == ReviewRunStatus.FAILED,
                ReviewRunRow.failed_stage == ReviewRunStatus.PUBLISHING,
                ~published.exists(),
                # Время падения - `last_progress_at` последнего перехода, не создание.
                or_(last_published_at.is_(None), ReviewRunRow.last_progress_at > last_published_at),
            )
            .order_by(ReviewRunRow.created_at, ReviewRunRow.id)
        ).all()
        return [m.review_run_to_domain(row) for row in rows]

    def list_changed(
        self,
        after: FeedCursor,
        recheck_from: dt.datetime,
        repository_ids: Collection[UUID],
        limit: int,
    ) -> list[ReviewRunListEntry]:
        """Один запрос: id обеих частей — `UNION ALL` двух выборок, к ним запрос на
        изменение, репозиторий и число находок `COUNT … GROUP BY` по пачке.

        `LIMIT` — только у части новее курсора. Окно перепроверки читается целиком: оно и
        так ограничено `RECHECK` по времени, а с `LIMIT` запоздавшая строка за первыми
        `limit` уже виденными в окне не вернулась бы никогда. Обе части идут по индексу
        `(updated_at, id)`.
        """
        if not repository_ids:
            return []
        key = tuple_(ReviewRunRow.updated_at, ReviewRunRow.id)
        cursor = tuple_(literal(after.updated_at), literal(after.id))
        visible = (
            select(ReviewRunRow.id)
            .join(MergeRequestRow, MergeRequestRow.id == ReviewRunRow.merge_request_id)
            .where(MergeRequestRow.repository_id.in_(repository_ids))
            .order_by(ReviewRunRow.updated_at, ReviewRunRow.id)
        )
        fresh = visible.where(key > cursor).limit(limit).subquery("fresh")
        late = visible.where(ReviewRunRow.updated_at > recheck_from, key <= cursor).subquery("late")
        picked = union_all(select(fresh.c.id), select(late.c.id)).cte("picked")
        return self._entries(picked, ReviewRunRow.updated_at, ReviewRunRow.id)

    def list_page(self, run_filter: RunListFilter, limit: int, offset: int) -> RunListPage:
        """Два запроса в одной транзакции REPEATABLE READ: `COUNT` под фильтрами и страница строками
        списка, как у `list_changed`.

        Порядок страницы — по индексу `(created_at, id)`. Подстрока `q` ищется `ILIKE` без
        индекса, по прогонам видимых репозиториев; `%` и `_` из ввода экранируются
        (`autoescape`) и шаблоном не становятся.
        """
        if not run_filter.repository_ids or run_filter.statuses == frozenset():
            return RunListPage(entries=[], total=0)
        matching = (
            select(ReviewRunRow.id)
            .join(MergeRequestRow, MergeRequestRow.id == ReviewRunRow.merge_request_id)
            .where(*_run_list_conditions(run_filter))
        )
        # COUNT и страница - один снимок: в READ COMMITTED прогон, закоммиченный между ними,
        # попал бы на страницу, но не в total. Уровень ставится до первого запроса транзакции;
        # SQLAlchemy возвращает соединению прежний уровень при возврате в пул.
        self._session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        total = self._session.scalar(select(func.count()).select_from(matching.subquery()))
        newest_first = (ReviewRunRow.created_at.desc(), ReviewRunRow.id.desc())
        page = matching.order_by(*newest_first).limit(limit).offset(offset).cte("page")
        return RunListPage(entries=self._entries(page, *newest_first), total=total or 0)

    def _entries(self, picked: CTE, *order_by: ColumnElement[Any] | InstrumentedAttribute[Any]) -> list[ReviewRunListEntry]:
        """Прогоны из `picked` строками списка: запрос на изменение, репозиторий и число находок.

        CTE, а не подзапрос: на неё ссылаются и выборка, и подсчёт находок, а CTE с
        двумя ссылками PostgreSQL вычисляет один раз.
        """
        counts = (
            select(FindingRow.review_run_id, func.count().label("findings_count"))
            .where(FindingRow.review_run_id.in_(select(picked.c.id)))
            .group_by(FindingRow.review_run_id)
            .subquery("counts")
        )
        rows = self._session.execute(
            select(
                ReviewRunRow,
                MergeRequestRow,
                RepositoryRow,
                func.coalesce(counts.c.findings_count, 0),
            )
            .join(picked, picked.c.id == ReviewRunRow.id)
            .join(MergeRequestRow, MergeRequestRow.id == ReviewRunRow.merge_request_id)
            .join(RepositoryRow, RepositoryRow.id == MergeRequestRow.repository_id)
            .outerjoin(counts, counts.c.review_run_id == ReviewRunRow.id)
            .order_by(*order_by)
        ).all()
        return [
            ReviewRunListEntry(
                run=m.review_run_to_domain(run),
                merge_request=m.merge_request_to_domain(merge_request),
                repository=m.repository_to_domain(repository),
                findings_count=findings_count,
            )
            for run, merge_request, repository, findings_count in rows
        ]

    def add(self, run: ReviewRun) -> None:
        """Вставить прогон, перенеся все поля, которые в нём уже есть.

        При вставке сущность — единственный источник, в том числе для прогона,
        восстановленного из прошлой попытки, поэтому поля результата и счётчик
        отклонённых переносятся здесь, хотя `update` счётчик не трогает.

        `created_at` и `updated_at` переносятся тоже: время прогона выдаёт
        `now`, переданный use case'у, и публичная проекция `ReviewJob` отдаёт
        именно его. Серверный дефолт `func.now()` остался бы вторым источником
        времени, разъезжающимся с сущностью на миллисекунды.
        """
        self._session.add(
            ReviewRunRow(
                id=run.id,
                merge_request_id=run.merge_request_id,
                head_sha=run.head_sha,
                base_sha=run.base_sha,
                status=run.status,
                trigger=run.trigger,
                last_progress_at=run.last_progress_at,
                created_at=run.created_at,
                updated_at=run.updated_at,
                failure_reason=run.failure_reason,
                failed_stage=run.failed_stage,
                model=run.model,
                tokens_used=run.tokens_used,
                duration_seconds=run.duration_seconds,
                rejected_findings=run.rejected_findings,
                verification=run.verification,
                verifier_dropped_findings=run.verifier_dropped_findings,
            )
        )
        self._notify(run)

    def update(self, run: ReviewRun) -> None:
        """Записать прогресс прогона, отклонив статус, запрещённый машиной состояний.

        Единственное правило в этом классе, и оно делегировано: таблицей
        переходов владеет `next_status`, поэтому адаптер не может разойтись с
        доменом.

        Счётчики отсева (`rejected_findings`, `verifier_dropped_findings`) и
        итог верификации считает пайплайн чистыми функциями и приносит в
        сущности, поэтому ими, как и всем остальным, владеет вызывающий.
        """
        # Блокировка строки: приём коммитит отмену параллельно переходу воркера (#222),
        # и без неё переход по прочитанному статусу перезаписал бы `cancelled`.
        # populate_existing: строка могла остаться в identity map от чтения до блокировки.
        row = self._session.get(
            ReviewRunRow, run.id, with_for_update=True, populate_existing=True
        )
        if row is None:
            raise LookupError(f"review run {run.id} is not stored")
        # Терминальная строка проходит через next_status, даже если статус не
        # изменился: одного сравнения на равенство хватило бы, чтобы повторно
        # доставленное обновление перезаписало стоимость завершённого прогона.
        if row.status in TERMINAL_STATUSES or row.status != run.status:
            verdict = next_status(row.status, run.status)
            if not verdict.ok:
                raise ValueError(verdict.error)
        row.status = run.status
        row.last_progress_at = run.last_progress_at
        row.failure_reason = run.failure_reason
        row.failed_stage = run.failed_stage
        row.model = run.model
        row.tokens_used = run.tokens_used
        row.duration_seconds = run.duration_seconds
        row.rejected_findings = run.rejected_findings
        row.verification = run.verification
        row.verifier_dropped_findings = run.verifier_dropped_findings
        self._notify(run)

    def _notify(self, run: ReviewRun) -> None:
        """Уведомить поток событий: `pg_notify` в той же транзакции уходит только при коммите.

        Через соединение сессии, а не `Session.execute`: тот запустил бы autoflush и
        поменял момент, когда пишутся строки вызывающего, — порядок вставок остаётся прежним.
        """
        self._session.connection().execute(
            text("SELECT pg_notify(:channel, :run_id)"),
            {"channel": REVIEW_RUNS_CHANNEL, "run_id": str(run.id)},
        )


class SqlAlchemyReviewRunStepRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_run(self, review_run_id: UUID) -> list[ReviewRunStep]:
        rows = self._session.scalars(
            select(ReviewRunStepRow)
            .where(ReviewRunStepRow.review_run_id == review_run_id)
            # Порядок начала; id — UUIDv7, разводит шаги с одним временем.
            .order_by(ReviewRunStepRow.started_at, ReviewRunStepRow.id)
        ).all()
        return [m.review_run_step_to_domain(row) for row in rows]

    def add(self, step: ReviewRunStep) -> None:
        self._session.add(
            ReviewRunStepRow(
                id=step.id,
                review_run_id=step.review_run_id,
                stage=step.stage,
                status=step.status,
                started_at=step.started_at,
                duration_seconds=step.duration_seconds,
                tokens_used=step.tokens_used,
                error_code=step.error_code,
            )
        )

    def update(self, step: ReviewRunStep) -> None:
        row = self._session.get(ReviewRunStepRow, step.id)
        if row is None:
            raise LookupError(f"review run step {step.id} is not stored")
        # Прогон, этап и начало у шага не меняются: меняется только исход.
        row.status = step.status
        row.duration_seconds = step.duration_seconds
        row.tokens_used = step.tokens_used
        row.error_code = step.error_code


class SqlAlchemyContextPayloadRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_run(self, review_run_id: UUID) -> list[ContextPayload]:
        rows = self._session.scalars(
            select(ContextPayloadRow)
            .where(ContextPayloadRow.review_run_id == review_run_id)
            .order_by(ContextPayloadRow.chunk_index)
        ).all()
        return [m.context_payload_to_domain(row) for row in rows]

    def add(self, payload: ContextPayload) -> None:
        """Записать пакет, заменив секреты в теле меткой (#28).

        Единственное правило в этом классе, и оно делегировано `redact_secrets`.
        Модели пакет уходит из памяти, а не отсюда, — секрет она видит; фейк
        хранит тело как есть.
        """
        self._session.add(
            ContextPayloadRow(
                id=payload.id,
                review_run_id=payload.review_run_id,
                chunk_index=payload.chunk_index,
                tiers=list(payload.tiers),
                file_paths=list(payload.file_paths),
                token_count=payload.token_count,
                content_sha256=payload.content_sha256,
                # Новая структура, а не ссылка на словарь сущности; секреты в базу не идут.
                body=redact_secrets(payload.body),
            )
        )


class SqlAlchemyFindingRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_run(self, review_run_id: UUID) -> list[Finding]:
        rows = self._session.scalars(
            select(FindingRow)
            .where(FindingRow.review_run_id == review_run_id)
            # Без ORDER BY Postgres порядка не обещает и меняет его после
            # UPDATE и vacuum; id — UUIDv7, упорядочены по времени создания.
            .order_by(FindingRow.created_at, FindingRow.id)
        ).all()
        return [m.finding_to_domain(row) for row in rows]

    def add(self, finding: Finding) -> None:
        self._session.add(
            FindingRow(
                id=finding.id,
                review_run_id=finding.review_run_id,
                file_path=finding.anchor.file_path,
                side=finding.anchor.side,
                old_line=finding.anchor.old_line,
                new_line=finding.anchor.new_line,
                category=finding.category,
                severity=finding.severity,
                message=finding.message,
                suggestion=finding.suggestion,
                confidence=finding.confidence,
                rule_name=finding.rule.name if finding.rule else None,
                rule_check=finding.rule.check if finding.rule else None,
                rule_check_text=finding.rule.check_text if finding.rule else None,
                covered_by_finding_id=finding.covered_by_finding_id,
            )
        )

    def mark_covered(self, finding_id: UUID, covered_by_finding_id: UUID) -> None:
        row = self._session.get(FindingRow, finding_id)
        if row is None:
            raise LookupError(f"finding {finding_id} is not stored")
        row.covered_by_finding_id = covered_by_finding_id


class SqlAlchemyPublishedCommentRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_run(self, review_run_id: UUID) -> list[PublishedComment]:
        rows = self._session.scalars(
            select(PublishedCommentRow)
            .where(PublishedCommentRow.review_run_id == review_run_id)
            # Порядок публикации; id разводит комментарии с одним временем.
            .order_by(PublishedCommentRow.published_at, PublishedCommentRow.id)
        ).all()
        return [m.published_comment_to_domain(row) for row in rows]

    def list_open_threads_for_merge_request(
        self, merge_request_id: UUID
    ) -> list[OpenReviewThread]:
        return [
            OpenReviewThread(
                finding=m.finding_to_domain(finding_row),
                root_provider_comment_id=comment_row.provider_comment_id,
                root_published_comment_id=comment_row.id,
            )
            for comment_row, finding_row in self._inline_threads(
                merge_request_id, PublishedCommentRow.thread_resolved.is_(False)
            )
        ]

    def list_threads_for_merge_request(self, merge_request_id: UUID) -> list[BotThread]:
        return [
            BotThread(
                finding=m.finding_to_domain(finding_row),
                root_provider_comment_id=comment_row.provider_comment_id,
                root_published_comment_id=comment_row.id,
                resolved=comment_row.thread_resolved,
                resolved_by_bot=comment_row.thread_resolved_by_bot,
            )
            for comment_row, finding_row in self._inline_threads(merge_request_id)
        ]

    def _inline_threads(
        self, merge_request_id: UUID, *conditions: ColumnElement[bool]
    ) -> Sequence[Row[PublishedCommentRow, FindingRow]]:
        """Корневые inline-комментарии бота на PR с их находками, в порядке публикации."""
        stmt = (
            select(PublishedCommentRow, FindingRow)
            .join(ReviewRunRow, PublishedCommentRow.review_run_id == ReviewRunRow.id)
            .join(FindingRow, PublishedCommentRow.finding_id == FindingRow.id)
            .where(
                ReviewRunRow.merge_request_id == merge_request_id,
                PublishedCommentRow.kind == CommentKind.INLINE,
                *conditions,
            )
            .order_by(PublishedCommentRow.published_at, PublishedCommentRow.id)
        )
        return self._session.execute(stmt).all()

    def get(self, published_comment_id: UUID) -> PublishedComment | None:
        row = self._session.get(PublishedCommentRow, published_comment_id)
        return m.published_comment_to_domain(row) if row is not None else None

    def find_by_provider_comment_id(self, provider_comment_id: str) -> PublishedComment | None:
        row = self._session.scalars(
            select(PublishedCommentRow).where(
                PublishedCommentRow.provider_comment_id == provider_comment_id
            )
        ).first()
        return m.published_comment_to_domain(row) if row is not None else None

    def add(self, comment: PublishedComment) -> None:
        self._session.add(
            PublishedCommentRow(
                id=comment.id,
                review_run_id=comment.review_run_id,
                finding_id=comment.finding_id,
                provider_comment_id=comment.provider_comment_id,
                kind=comment.kind,
                published_at=comment.published_at,
                thread_resolved=comment.thread_resolved,
                thread_resolved_by_bot=comment.thread_resolved_by_bot,
                in_reply_to_provider_comment_id=comment.in_reply_to_provider_comment_id,
                feedback_positive=comment.feedback_positive,
                feedback_negative=comment.feedback_negative,
            )
        )

    def update(self, comment: PublishedComment) -> None:
        row = self._session.get(PublishedCommentRow, comment.id)
        if row is None:
            raise LookupError(f"published comment {comment.id} is not stored")
        row.thread_resolved = comment.thread_resolved
        row.thread_resolved_by_bot = comment.thread_resolved_by_bot
        row.in_reply_to_provider_comment_id = comment.in_reply_to_provider_comment_id
        row.feedback_positive = comment.feedback_positive
        row.feedback_negative = comment.feedback_negative
        row.updated_at = comment.updated_at

    def record_feedback(self, provider_comment_id: str, *, positive: bool) -> bool:
        if positive:
            values = {
                PublishedCommentRow.feedback_positive: (
                    PublishedCommentRow.feedback_positive + 1
                )
            }
        else:
            values = {
                PublishedCommentRow.feedback_negative: (
                    PublishedCommentRow.feedback_negative + 1
                )
            }
        updated_id = self._session.scalars(
            update(PublishedCommentRow)
            .where(PublishedCommentRow.provider_comment_id == provider_comment_id)
            .values(values)
            .returning(PublishedCommentRow.id)
        ).first()
        return updated_id is not None


class SqlAlchemyUserRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: UUID) -> User | None:
        row = self._session.get(UserRow, user_id)
        return m.user_to_domain(row) if row else None

    def find_by_provider(self, provider: Provider, provider_user_id: str) -> User | None:
        row = self._session.scalars(
            select(UserRow).where(
                UserRow.provider == provider,
                UserRow.provider_user_id == provider_user_id,
            )
        ).one_or_none()
        return m.user_to_domain(row) if row else None

    def add(self, user: User) -> bool:
        # `ON CONFLICT DO NOTHING`: при параллельной вставке той же пары
        # вторая транзакция ждёт коммита первой и получает 0 строк, а не
        # `IntegrityError`.
        # `no_autoflush`: ожидающая сессия с этим пользователем не должна
        # уйти в базу раньше самого пользователя.
        with self._session.no_autoflush:
            inserted = self._session.execute(
                pg_insert(UserRow)
                .values(
                    id=user.id,
                    provider=user.provider,
                    provider_user_id=user.provider_user_id,
                    login=user.login,
                    name=user.name,
                    avatar_url=user.avatar_url,
                    created_at=user.created_at,
                    updated_at=user.updated_at,
                )
                .on_conflict_do_nothing(constraint="uq_users_provider_user_id")
                .returning(UserRow.id)
            ).first()
        return inserted is not None

    def update(self, user: User) -> None:
        row = self._session.get(UserRow, user.id)
        if row is None:
            raise LookupError(f"user {user.id} is not stored")
        row.login = user.login
        row.name = user.name
        row.avatar_url = user.avatar_url
        row.updated_at = user.updated_at


class SqlAlchemySessionRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_token_hash(self, token_hash: bytes) -> UserSession | None:
        row = self._session.scalars(
            select(SessionRow).where(SessionRow.token_hash == token_hash)
        ).one_or_none()
        return m.session_to_domain(row) if row else None

    def add(self, session: UserSession) -> None:
        self._session.add(
            SessionRow(
                id=session.id,
                token_hash=session.token_hash,
                user_id=session.user_id,
                created_at=session.created_at,
                expires_at=session.expires_at,
            )
        )

    def delete(self, session_id: UUID) -> None:
        self._session.execute(delete(SessionRow).where(SessionRow.id == session_id))

    def delete_expired(self, now: dt.datetime) -> int:
        result = self._session.execute(delete(SessionRow).where(SessionRow.expires_at <= now))
        return int(result.rowcount)  # type: ignore[attr-defined]


_ACCESS = "access_token"
# Предел ожидания чужой блокировки токена при refresh; дальше — `ProviderTokenBusy`.
_LOCK_TIMEOUT = "15s"
_REFRESH = "refresh_token"


class SqlAlchemyProviderTokenRepo:
    """Токен пользователя при сессии; шифрует при записи и расшифровывает при чтении.

    Без ключа (`cipher is None`) не пишет и не читает — `ProviderTokenUnavailable`:
    открытым токен в базу не попадает никогда.
    """

    def __init__(self, session: Session, cipher: TokenCipher | None) -> None:
        self._session = session
        self._cipher = cipher

    def get(self, session_id: UUID) -> ProviderToken | None:
        row = self._session.get(ProviderTokenRow, session_id)
        return self._to_domain(row) if row else None

    def exists(self, session_id: UUID) -> bool:
        return self._session.scalar(
            select(ProviderTokenRow.session_id).where(ProviderTokenRow.session_id == session_id)
        ) is not None

    def get_for_update(self, session_id: UUID) -> ProviderToken | None:
        # Ждём чужой refresh не дольше `_LOCK_TIMEOUT` (только эта транзакция):
        # его держатель ждёт GitHub, и бесконечное ожидание висело бы на пуле.
        self._session.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
        try:
            # populate_existing: строка могла остаться в identity map от `get`
            # до блокировки, а читать надо то, что записал победитель гонки.
            row = self._session.scalars(
                select(ProviderTokenRow)
                .where(ProviderTokenRow.session_id == session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one_or_none()
        except OperationalError as error:
            if isinstance(error.orig, LockNotAvailable):
                raise ProviderTokenBusy("токен сессии обновляет параллельный запрос") from None
            raise
        return self._to_domain(row) if row else None

    def save(self, session_id: UUID, token: ProviderToken) -> None:
        cipher = self._require_cipher()
        row = self._session.get(ProviderTokenRow, session_id)
        if row is None:
            row = ProviderTokenRow(session_id=session_id)
            self._session.add(row)
        row.access_token_encrypted = cipher.encrypt(token.access_token.reveal(), session_id, _ACCESS)
        row.access_expires_at = token.access_expires_at
        row.refresh_token_encrypted = (
            None
            if token.refresh_token is None
            else cipher.encrypt(token.refresh_token.reveal(), session_id, _REFRESH)
        )
        row.refresh_expires_at = token.refresh_expires_at

    def _to_domain(self, row: ProviderTokenRow) -> ProviderToken:
        cipher = self._require_cipher()
        refresh = row.refresh_token_encrypted
        return ProviderToken(
            access_token=Secret(cipher.decrypt(row.access_token_encrypted, row.session_id, _ACCESS)),
            access_expires_at=row.access_expires_at,
            refresh_token=None if refresh is None else Secret(cipher.decrypt(refresh, row.session_id, _REFRESH)),
            refresh_expires_at=row.refresh_expires_at,
        )

    def _require_cipher(self) -> TokenCipher:
        if self._cipher is None:
            raise ProviderTokenUnavailable("не задан USER_TOKEN_ENCRYPTION_KEY")
        return self._cipher


_SIMULATION_ACTIVE = SimulationRow.status.not_in(SIMULATION_TERMINAL_STATUSES)


class SqlAlchemySimulationRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, simulation_id: UUID) -> Simulation | None:
        row = self._session.get(SimulationRow, simulation_id)
        return m.simulation_to_domain(row) if row else None

    def get_for_user(self, simulation_id: UUID, user_id: UUID) -> Simulation | None:
        row = self._session.scalars(
            select(SimulationRow).where(
                SimulationRow.id == simulation_id, SimulationRow.user_id == user_id
            )
        ).one_or_none()
        return m.simulation_to_domain(row) if row else None

    def add(self, simulation: Simulation) -> bool:
        # `ON CONFLICT DO NOTHING` по частичному индексу: параллельный запуск
        # ждёт коммита первого и получает 0 строк, а не `IntegrityError`.
        inserted = self._session.execute(
            pg_insert(SimulationRow)
            .values(
                id=simulation.id,
                user_id=simulation.user_id,
                created_at=simulation.created_at,
                **self._columns(simulation),
            )
            .on_conflict_do_nothing(
                index_elements=[SimulationRow.user_id], index_where=_SIMULATION_ACTIVE
            )
            .returning(SimulationRow.id)
        ).first()
        return inserted is not None

    def update(self, simulation: Simulation) -> bool:
        """Записать ход или итог, отклонив переход, запрещённый машиной состояний.

        Правило делегировано `next_simulation_status`. Блокировка строки: перевод
        зависших в проходе обслуживания коммитит итог параллельно воркеру.
        """
        row = self._session.get(
            SimulationRow, simulation.id, with_for_update=True, populate_existing=True
        )
        if row is None:
            raise LookupError(f"simulation {simulation.id} is not stored")
        if row.status in SIMULATION_TERMINAL_STATUSES:
            return False
        if row.status != simulation.status:
            verdict = next_simulation_status(row.status, simulation.status)
            if not verdict.ok:
                raise ValueError(verdict.error)
        for column, value in self._columns(simulation).items():
            setattr(row, column, value)
        # Время перехода - из сущности, как и при вставке: зависание сверяется с часами
        # приложения. Без пометки равное прежнему значение не попало бы в UPDATE, и
        # `onupdate` колонки подставил бы часы базы.
        flag_modified(row, "updated_at")
        return True

    @staticmethod
    def _columns(simulation: Simulation) -> dict[str, Any]:
        """Изменяемые колонки строки; контекст результата - без секретов (#28, design D10).

        Второе место правила в этом классе, и оно делегировано `redact_secrets`:
        модели вход ушёл из памяти воркера, секрет она видела; фейк хранит как есть.
        """
        result = None
        if simulation.result is not None:
            result = m.simulation_result_to_json(simulation.result)
            result["context"] = redact_secrets(result["context"])
        return {
            "status": simulation.status,
            "source_kind": simulation.source_kind,
            "file_path": simulation.file_path,
            "source": simulation.source,
            "rules": simulation.rules,
            "result": result,
            "model": simulation.model,
            "tokens_used": simulation.tokens_used,
            "verification": simulation.verification,
            "error_code": simulation.error_code,
            "expires_at": simulation.expires_at,
            "updated_at": simulation.updated_at,
        }

    def has_active(self, user_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(
                    select(SimulationRow.id)
                    .where(SimulationRow.user_id == user_id, _SIMULATION_ACTIVE)
                    .exists()
                )
            )
        )

    def created_since(self, user_id: UUID, since: dt.datetime) -> list[dt.datetime]:
        return list(
            self._session.scalars(
                select(SimulationRow.created_at)
                .where(SimulationRow.user_id == user_id, SimulationRow.created_at > since)
                .order_by(SimulationRow.created_at)
            )
        )

    def fail_stale(self, before: dt.datetime, now: dt.datetime) -> int:
        # Тем же `UPDATE` стирается вход: итоговую строку со входом не пропустит CHECK.
        result = self._session.execute(
            update(SimulationRow)
            .where(_SIMULATION_ACTIVE, SimulationRow.updated_at < before)
            .values(
                status=SimulationStatus.FAILED,
                error_code=SIMULATION_STALE,
                source=None,
                rules=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    def delete_expired(self, now: dt.datetime) -> int:
        result = self._session.execute(
            delete(SimulationRow)
            .where(SimulationRow.expires_at <= now)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]
