"""Сценарий сборки контекста прогона: всё I/O в одном месте (design D7).

`fetch_diff` → `parse_diff` → правила целевой ветки → содержимое изменённых
файлов на `head_sha` и их контуры → чистая `assemble_context`. Сбой
получения диффа пробрасывается: без диффа ревьюить нечего, прогон уходит в
`failed` и DLQ. Правила и AST — best-effort: сбой на них деградирует пакет
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
from app.domain.vcs_errors import VcsError, VcsFileTooLargeError

logger = logging.getLogger(__name__)


def build_review_context(
    job: ReviewJob,
    *,
    vcs: VcsGateway,
    extractor: DefinitionExtractor,
    cache: CacheStore,
    limits: ContextLimits,
    review_run_id: UUID,
    now: datetime,
    new_id: Callable[[], UUID],
) -> AssembledContext:
    diff = vcs.fetch_diff(
        job.repository_full_name, job.base_sha, job.head_sha, job.installation_id
    )
    files = parse_diff(diff)
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
        limits=limits,
        review_run_id=review_run_id,
        now=now,
        new_id=new_id,
    )


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
