"""Дифф прогона для кабинета (change `review-run-diff`, D5).

Порядок — от дешёвого к дорогому, и попадание в кэш не ходит в сеть: нет
`base_sha` — отказ сразу; затем кэш по паре коммитов; затем номер установки
приложения и дифф у провайдера. Видимость прогона проверяет вызывающий до
вызова, поэтому пользователя в ключе кэша нет.

В кэше — сырой дифф, на попадании он разбирается заново: повторный разбор
дешевле JSON-кодека представления, а запись втрое меньше (замер в PR #308).
Проверки лимитов на попадании нет: в кэш пишется только дифф, прошедший оба
лимита (байты и строки), а кэш живёт в памяти процесса и пропадает при рестарте
вместе со сменой лимита.

Модель ответа собирает слой API. TTL — константа: запись по паре коммитов не
устаревает, час лишь освобождает память от прогонов, которые никто не открывает.
"""

import logging

from app.application.ports.cache_store import CacheStore
from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.vcs_gateway import VcsGateway
from app.domain.diff_parser import parse_diff
from app.domain.diff_view import DiffFileView, build_diff_view
from app.domain.entities import Repository, ReviewRun
from app.domain.vcs_errors import (
    DiffTooLargeError,
    VcsFileTooLargeError,
    VcsNotFoundError,
)

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "run-diff:v1"
_CACHE_TTL_SECONDS = 3600
# Потолок строк сырого диффа: байтовый лимит строк не ограничивает (2 МиБ однобайтовых
# строк — около миллиона строк, и разбор с представлением не помещаются в память контейнера).
RUN_DIFF_MAX_LINES = 50_000


class RunDiffUnavailable(Exception):
    """У прогона нет `base_sha`: пары коммитов для диффа нет."""


class RepositoryNotInstalled(Exception):
    """Приложение не установлено на репозиторий прогона или установка приостановлена."""


class RunCommitNotFound(Exception):
    """Провайдер не знает коммит прогона (`404` на сравнение): например, после force-push.

    Установка только что найдена, значит репозиторий доступен — пропал коммит.
    """


def run_diff_cache_key(repository: Repository, base_sha: str, head_sha: str) -> str:
    """Внутренний id репозитория не меняется при переименовании и не несёт данных пользователя."""
    return f"{_CACHE_PREFIX}:{repository.id}:{base_sha}:{head_sha}"


def load_run_diff(
    run: ReviewRun,
    repository: Repository,
    *,
    installations: InstallationCatalog,
    vcs: VcsGateway,
    cache: CacheStore,
    max_bytes: int,
) -> tuple[DiffFileView, ...]:
    """Представление диффа `base_sha...head_sha` прогона.

    Отказы не кэшируются: `RunDiffUnavailable`, `RepositoryNotInstalled`,
    `DiffTooLargeError` (больше `max_bytes` байт, больше `RUN_DIFF_MAX_LINES` строк
    или провайдер отказался строить дифф), `RunCommitNotFound`; прочий `VcsError` — наружу как есть.
    Сбой кэша — промах.
    """
    if run.base_sha is None:
        raise RunDiffUnavailable(f"у прогона {run.id} нет base_sha")
    key = run_diff_cache_key(repository, run.base_sha, run.head_sha)
    cached = _read_cache(cache, key)
    if cached is not None:
        return build_diff_view(parse_diff(cached))
    installation_id = installations.repository_installation_id(repository.full_name)
    if installation_id is None:
        raise RepositoryNotInstalled(f"приложение не установлено на {repository.full_name}")
    try:
        raw = vcs.fetch_diff(
            repository.full_name, run.base_sha, run.head_sha, installation_id, max_bytes=max_bytes
        )
    except VcsFileTooLargeError as error:
        raise DiffTooLargeError(f"дифф прогона {run.id} больше {max_bytes} байт") from error
    except VcsNotFoundError as error:
        raise RunCommitNotFound(f"провайдер не знает коммиты прогона {run.id}") from error
    # Считаем строки до разбора: разбор и представление и есть то, что не помещается в память.
    if raw.count("\n") > RUN_DIFF_MAX_LINES:
        raise DiffTooLargeError(f"дифф прогона {run.id} больше {RUN_DIFF_MAX_LINES} строк")
    view = build_diff_view(parse_diff(raw))
    _write_cache(cache, key, raw)
    return view


def _read_cache(cache: CacheStore, key: str) -> str | None:
    try:
        return cache.get(key)
    except Exception as error:  # noqa: BLE001 — сбой хранилища — промах, а не отказ ручки
        logger.warning("кэш диффа прогона: не удалось прочитать %s: %s", key, type(error).__name__)
        return None


def _write_cache(cache: CacheStore, key: str, raw: str) -> None:
    try:
        cache.set(key, raw, _CACHE_TTL_SECONDS)
    except Exception as error:  # noqa: BLE001 — сбой хранилища не ломает ответ, дифф уже получен
        logger.warning("кэш диффа прогона: не удалось записать %s: %s", key, type(error).__name__)
