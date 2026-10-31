"""Дифф прогона для кабинета (change `review-run-diff`, D5).

Порядок — от дешёвого к дорогому, и попадание в кэш не ходит в сеть: нет
`base_sha` — отказ сразу; затем кэш по паре коммитов; затем номер установки
приложения и дифф у провайдера. Видимость прогона проверяет вызывающий до
вызова, поэтому пользователя в ключе кэша нет.

В кэше — JSON доменного представления, а не ответа API: на попадании не
нужен повторный разбор диффа, а модель ответа собирает слой API.
"""

import json
import logging
from dataclasses import asdict
from typing import Any

from app.application.ports.cache_store import CacheStore
from app.application.ports.installation_catalog import InstallationCatalog
from app.application.ports.vcs_gateway import VcsGateway
from app.domain.diff_parser import parse_diff
from app.domain.diff_view import DiffFileView, DiffHunkView, DiffLine, build_diff_view
from app.domain.entities import Repository, ReviewRun
from app.domain.enums import DiffFileStatus, DiffLineKind, Language
from app.domain.vcs_errors import (
    DiffTooLargeError,
    VcsFileTooLargeError,
    VcsNotFoundError,
)

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "run-diff:v1"


class RunDiffUnavailable(Exception):
    """У прогона нет `base_sha`: пары коммитов для диффа нет."""


class RepositoryNotInstalled(Exception):
    """Приложение не установлено на репозиторий или установка приостановлена."""


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
    ttl_seconds: int,
) -> tuple[DiffFileView, ...]:
    """Представление диффа `base_sha...head_sha` прогона.

    Отказы не кэшируются: `RunDiffUnavailable`, `RepositoryNotInstalled`,
    `DiffTooLargeError`, `RunCommitNotFound`; прочий `VcsError` — наружу как есть.
    Сбой кэша и битая запись — промах.
    """
    if run.base_sha is None:
        raise RunDiffUnavailable(f"у прогона {run.id} нет base_sha")
    key = run_diff_cache_key(repository, run.base_sha, run.head_sha)
    cached = _read_cache(cache, key)
    if cached is not None:
        return cached
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
    view = build_diff_view(parse_diff(raw))
    _write_cache(cache, key, view, ttl_seconds)
    return view


def _read_cache(cache: CacheStore, key: str) -> tuple[DiffFileView, ...] | None:
    try:
        raw = cache.get(key)
        if raw is None:
            return None
        return tuple(_file_from_json(item) for item in json.loads(raw))
    except Exception as error:  # noqa: BLE001 — битая запись и сбой хранилища — промах, а не отказ ручки
        # В лог ключ и тип ошибки, без exc_info: текст ошибки разбора повторяет значение из записи.
        logger.warning("кэш диффа прогона: не удалось прочитать %s: %s", key, type(error).__name__)
        return None


def _write_cache(cache: CacheStore, key: str, view: tuple[DiffFileView, ...], ttl: int) -> None:
    try:
        # ensure_ascii по умолчанию: лимит считает байты UTF-8, а не-ASCII строка в памяти — до 4 байт на символ.
        cache.set(key, json.dumps([asdict(f) for f in view]), ttl)
    except Exception as error:  # noqa: BLE001 — сбой хранилища не ломает ответ, дифф уже получен
        logger.warning("кэш диффа прогона: не удалось записать %s: %s", key, type(error).__name__)


def _file_from_json(data: dict[str, Any]) -> DiffFileView:
    language = data["language"]
    return DiffFileView(
        path=str(data["path"]),
        previous_path=data["previous_path"],
        status=DiffFileStatus(data["status"]),
        is_binary=bool(data["is_binary"]),
        language=None if language is None else Language(language),
        additions=int(data["additions"]),
        deletions=int(data["deletions"]),
        hunks=tuple(_hunk_from_json(h) for h in data["hunks"]),
    )


def _hunk_from_json(data: dict[str, Any]) -> DiffHunkView:
    return DiffHunkView(
        header=str(data["header"]),
        old_start=int(data["old_start"]),
        old_count=int(data["old_count"]),
        new_start=int(data["new_start"]),
        new_count=int(data["new_count"]),
        lines=tuple(
            DiffLine(
                kind=DiffLineKind(line["kind"]),
                old_line=line["old_line"],
                new_line=line["new_line"],
                content=str(line["content"]),
            )
            for line in data["lines"]
        ),
    )
