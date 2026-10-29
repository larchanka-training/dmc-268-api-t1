"""Что слою приложения позволено знать о хранении.

Структурные протоколы: адаптер никогда не импортирует этот модуль, и у слоя
инфраструктуры нет compile-time зависимости от слоя приложения. Каждая
сигнатура говорит в доменных типах: ниже нет ни сессии, ни engine, ни
SQLAlchemy.
"""

from collections.abc import Collection
from datetime import datetime
from typing import Protocol
from uuid import UUID

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
    Session,
    User,
)
from app.domain.enums import Provider
from app.domain.run_feed import FeedCursor
from app.domain.run_list import RunListFilter, RunListPage


class RepositoryRepo(Protocol):
    def get(self, repository_id: UUID) -> Repository | None: ...

    def find_by_provider(self, provider: Provider, provider_id: str) -> Repository | None: ...

    def list_by_provider_ids(self, provider: Provider, provider_ids: Collection[str]) -> list[Repository]:
        """Зарегистрированные репозитории провайдера из списка его id; незарегистрированных нет."""
        ...

    def add(self, repository: Repository) -> bool:
        """Вставить репозиторий; `False` — пара провайдер + id уже занята.

        Два параллельных подключения одного репозитория не должны давать
        ошибку базы: проигравший получает `False` и отвечает конфликтом.
        """
        ...

    def update(self, repository: Repository) -> None: ...


class MergeRequestRepo(Protocol):
    def get(self, merge_request_id: UUID) -> MergeRequest | None: ...

    def find_by_number(self, repository_id: UUID, number: int) -> MergeRequest | None: ...

    def list_for_repository(
        self, repository_id: UUID, limit: int, offset: int
    ) -> list[MergeRequest]: ...

    def add(self, merge_request: MergeRequest) -> None: ...

    def update(self, merge_request: MergeRequest) -> None: ...


class ReviewRunRepo(Protocol):
    def get(self, run_id: UUID) -> ReviewRun | None: ...

    def find_active(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None: ...

    def find_reviewed(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        """Самый поздний прогон по вебхуку на этот коммит в `REVIEWED_STATUSES`."""
        ...

    def find_latest(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        """Самый поздний прогон на этот коммит в любом статусе: им решается, чьё
        ревью сейчас отвечает за merge gate head-коммита."""
        ...

    def list_unfinished(self) -> list[ReviewRun]: ...

    def list_unpublished_failures(self, merge_request_id: UUID) -> list[ReviewRun]:
        """Прогоны PR в `failed` с этапом сбоя `publishing` и без единой записи в
        `published_comments`: их ревью могло остаться на хостинге без следа в базе.

        Только упавшие позже последней записи PR в `published_comments`: прогон,
        упавший до чужой успешной публикации, уже проверяли, и каждый следующий
        прогон не листает ради него ревью PR заново. Сравнивается время падения, а
        не создания: прогон, умерший на `publishing`, выметатель переводит в
        `failed` позже, и прогон, опубликованный тем временем, его не видел."""
        ...

    def list_changed(
        self,
        after: FeedCursor,
        recheck_from: datetime,
        repository_ids: Collection[UUID],
        limit: int,
    ) -> list[ReviewRunListEntry]:
        """Изменённые прогоны репозиториев `repository_ids` — строками списка, одним запросом.

        Две части: до `limit` прогонов новее `after` по `(updated_at, id)` и все прогоны из
        окна перепроверки — `updated_at` позже `recheck_from`, но не новее `after` (окно
        ограничено временем, а не числом строк). Порядок — по `(updated_at, id)`. Пустой
        `repository_ids` — пустой ответ.
        """
        ...

    def list_page(self, run_filter: RunListFilter, limit: int, offset: int) -> RunListPage:
        """Страница прогонов под фильтром — строками списка — и их общее число.

        Порядок — `created_at` по убыванию, при равном — `id` по убыванию. Число
        запросов к базе не зависит от размера страницы. Пустой `repository_ids` или
        пустой набор статусов — пустая страница и `total = 0`.
        """
        ...

    def add(self, run: ReviewRun) -> None: ...

    def update(self, run: ReviewRun) -> None: ...


class ReviewRunStepRepo(Protocol):
    def list_for_run(self, review_run_id: UUID) -> list[ReviewRunStep]:
        """Шаги прогона по порядку начала; при равном начале - по id."""
        ...

    def add(self, step: ReviewRunStep) -> None: ...

    def update(self, step: ReviewRunStep) -> None: ...


class ContextPayloadRepo(Protocol):
    def list_for_run(self, review_run_id: UUID) -> list[ContextPayload]: ...

    def find_by_digest(self, content_sha256: str) -> ContextPayload | None: ...

    def add(self, payload: ContextPayload) -> None: ...


class FindingRepo(Protocol):
    def list_for_run(self, review_run_id: UUID) -> list[Finding]: ...

    def add(self, finding: Finding) -> None: ...

    def mark_covered(self, finding_id: UUID, covered_by_finding_id: UUID) -> None:
        """Связать находку с находкой, чей тред бота уже о той же проблеме."""
        ...


class PublishedCommentRepo(Protocol):
    def list_for_run(self, review_run_id: UUID) -> list[PublishedComment]: ...

    def list_open_threads_for_merge_request(
        self, merge_request_id: UUID
    ) -> list[OpenReviewThread]: ...

    def list_threads_for_merge_request(self, merge_request_id: UUID) -> list[BotThread]:
        """Все inline-треды бота на PR, открытые и закрытые, в порядке публикации."""
        ...

    def get(self, published_comment_id: UUID) -> PublishedComment | None: ...

    def find_by_provider_comment_id(self, provider_comment_id: str) -> PublishedComment | None: ...

    def add(self, comment: PublishedComment) -> None: ...

    def update(self, comment: PublishedComment) -> None: ...

    def record_feedback(self, provider_comment_id: str, *, positive: bool) -> bool: ...


class UserRepo(Protocol):
    def get(self, user_id: UUID) -> User | None: ...

    def find_by_provider(self, provider: Provider, provider_user_id: str) -> User | None: ...

    def add(self, user: User) -> bool:
        """Вставить пользователя; `False` — пара провайдер + аккаунт уже занята.

        Два параллельных первых входа одного аккаунта не должны давать ошибку
        базы: проигравший получает `False` и обновляет найденную строку.
        """
        ...

    def update(self, user: User) -> None: ...


class SessionRepo(Protocol):
    def find_by_token_hash(self, token_hash: bytes) -> Session | None: ...

    def add(self, session: Session) -> None: ...

    def delete(self, session_id: UUID) -> None:
        """Удалить сессию; отсутствующая — не ошибка: выход идемпотентен."""
        ...

    def delete_expired(self, now: datetime) -> int:
        """Удалить сессии, срок которых истёк к `now`; вернуть их число."""
        ...


class ProviderTokenRepo(Protocol):
    """Токен пользователя у провайдера при сессии входа.

    Шифрование — дело адаптера: сюда и отсюда токен идёт открытым, в базе —
    только шифротекст. Удаляется вместе с сессией (каскадом): отдельного
    `delete` нет. Нет ключа шифрования или запись под другим ключом —
    `ProviderTokenUnavailable`.
    """

    def get(self, session_id: UUID) -> ProviderToken | None: ...

    def exists(self, session_id: UUID) -> bool:
        """Есть ли у сессии токен — без расшифровки и без ключа: проверка сессии на
        каждом запросе (`authenticate`) не должна зависеть от шифрования."""
        ...

    def get_for_update(self, session_id: UUID) -> ProviderToken | None:
        """Прочитать и держать запись до конца транзакции: refresh одной сессии —
        по одному за раз (design D4)."""
        ...

    def save(self, session_id: UUID, token: ProviderToken) -> None:
        """Записать или заменить токен сессии."""
        ...
