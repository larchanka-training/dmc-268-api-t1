"""Сценарий сборки контекста прогона: всё I/O в одном месте (design D7).

`fetch_review_diff` → `parse_diff` → контекст PR (заголовок, описание,
сообщения коммитов) → правила целевой ветки → содержимое изменённых файлов
на `head_sha` и их контуры → чистая `assemble_context`. Получение диффа —
отдельная функция: у прогона это отдельный шаг (`fetching_diff`), и дифф
достаётся здесь впервые — приём вебхука его не запрашивает. Сбой получения
диффа и дифф больше `diff_max_bytes` пробрасываются: без диффа ревьюить
нечего, прогон уходит в `failed` и DLQ.
Контекст PR, правила и AST — best-effort: сбой на них деградирует пакет
(причина — в `omitted` и `rules.status`), но не прогон.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import UUID

from app.application.context_assembly import (
    AssembledContext,
    ContextLimits,
    FileAst,
    PrContext,
    SkippedAst,
    assemble_context,
)
from app.application.ports.cache_store import CacheStore
from app.application.ports.definition_extractor import DefinitionExtractor
from app.application.ports.vcs_gateway import VcsGateway
from app.application.repo_config_loader import load_repo_rules
from app.domain.code_context import language_for_path
from app.domain.diff_parser import filter_diff_files, parse_diff
from app.domain.entities import ParsedFile, ReviewJob
from app.domain.vcs_errors import DiffTooLargeError, VcsError, VcsFileTooLargeError

logger = logging.getLogger(__name__)


def fetch_review_diff(job: ReviewJob, vcs: VcsGateway, *, max_bytes: int) -> str:
    """Unified diff запроса на изменение между `base_sha` и `head_sha` задачи.

    Дифф больше `max_bytes` не дочитывается и не ревьюится по частям:
    `DiffTooLargeError`, прогон уходит в `failed` с понятной причиной.
    """
    try:
        return vcs.fetch_diff(
            job.repository_full_name,
            job.base_sha,
            job.head_sha,
            job.installation_id,
            max_bytes=max_bytes,
        )
    except VcsFileTooLargeError as error:
        raise DiffTooLargeError(
            f"дифф PR больше лимита {max_bytes} байт: ревью не выполняется"
        ) from error


def build_review_context(
    job: ReviewJob,
    diff: str,
    *,
    vcs: VcsGateway,
    extractor: DefinitionExtractor,
    cache: CacheStore,
    limits: ContextLimits,
    review_run_id: UUID,
    now: datetime,
    new_id: Callable[[], UUID],
) -> AssembledContext:
    files = parse_diff(diff)
    pr_context = _fetch_pr_context(job, vcs)
    rules = load_repo_rules(
        job.repository_full_name,
        job.base_sha,
        job.installation_id,
        vcs=vcs,
        cache=cache,
        max_bytes=limits.rules_max_bytes,
        cache_ttl_seconds=limits.rules_cache_ttl_seconds,
    )
    asts, skipped = _collect_asts(job, filter_diff_files(files), vcs, extractor, limits)
    return assemble_context(
        job,
        files,
        rules,
        asts,
        skipped,
        pr_context=pr_context,
        limits=limits,
        review_run_id=review_run_id,
        now=now,
        new_id=new_id,
    )


def _fetch_pr_context(job: ReviewJob, vcs: VcsGateway) -> PrContext:
    """Заголовок, описание и сообщения коммитов PR; недоступное - `None`."""
    title: str | None = None
    description: str | None = None
    commit_messages: tuple[str, ...] | None = None
    try:
        metadata = vcs.fetch_pr_metadata(
            job.repository_full_name, job.pull_request_number, job.installation_id
        )
        title, description = metadata.title, metadata.description
    except VcsError:
        logger.warning("метаданные PR %s недоступны для контекста", job.pull_request_number, exc_info=True)
    try:
        commit_messages = vcs.fetch_pr_commit_messages(
            job.repository_full_name, job.pull_request_number, job.installation_id
        )
    except VcsError:
        logger.warning("коммиты PR %s недоступны для контекста", job.pull_request_number, exc_info=True)
    return PrContext(title=title, description=description, commit_messages=commit_messages)


def _collect_asts(
    job: ReviewJob,
    files: Sequence[ParsedFile],
    vcs: VcsGateway,
    extractor: DefinitionExtractor,
    limits: ContextLimits,
) -> tuple[list[FileAst], list[SkippedAst]]:
    """Контуры изменённых файлов с поддержанным языком, в пределах лимитов.

    Файл неподдержанного языка, удалённый, бинарный или без hunk'ов не
    довыбирается и не упоминается: AST для него не предусмотрен. Сверх
    `ast_max_files`, отсутствующий, недоступный, слишком большой или
    сломавший парсер файл — в `SkippedAst` с причиной.
    """
    candidates = [
        (f, language)
        for f in files
        if not f.is_binary
        and not f.is_deleted
        and f.hunks
        and (language := language_for_path(f.file_path)) is not None
    ]
    asts: list[FileAst] = []
    skipped = [SkippedAst(f.file_path, "file_limit") for f, _ in candidates[limits.ast_max_files :]]
    for parsed, language in candidates[: limits.ast_max_files]:
        path = parsed.file_path
        try:
            source = vcs.fetch_file(
                job.repository_full_name,
                path,
                job.head_sha,
                job.installation_id,
                max_bytes=limits.ast_max_file_bytes,
            )
        except VcsFileTooLargeError:
            # Адаптер оборвал загрузку на лимите — тело в память не попало.
            skipped.append(SkippedAst(path, "too_large"))
            continue
        except VcsError:
            logger.warning("файл %s@%s недоступен для AST", path, job.head_sha, exc_info=True)
            skipped.append(SkippedAst(path, "unavailable"))
            continue
        if source is None:
            skipped.append(SkippedAst(path, "missing"))
            continue
        try:
            outline = extractor.extract(source, language)
        except Exception:
            logger.exception("парсер не справился с %s@%s", path, job.head_sha)
            skipped.append(SkippedAst(path, "parse_error"))
            continue
        asts.append(FileAst(path, language, outline))
    return asts, skipped
