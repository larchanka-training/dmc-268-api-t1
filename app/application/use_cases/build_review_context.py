"""Сценарий сборки контекста прогона: всё I/O в одном месте (design D7).

`fetch_review_diff` → `parse_diff` → контекст PR (заголовок, описание,
сообщения коммитов) → правила целевой ветки → содержимое изменённых файлов
на `head_sha` (для окон вокруг hunk'ов и AST) и их контуры → чистая `assemble_context`. Получение диффа —
отдельная функция: у прогона это отдельный шаг (`fetching_diff`), и дифф
достаётся здесь впервые — приём вебхука его не запрашивает. Сбой получения
диффа и дифф больше `diff_max_bytes` байт или `REVIEW_DIFF_MAX_LINES` строк
пробрасываются: без диффа ревьюить нечего, прогон уходит в `failed` и DLQ.
Контекст PR, правила, тексты файлов и AST — best-effort: сбой на них деградирует пакет
(причина — в `omitted` и `rules.status`), но не прогон.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
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

# Потолок строк сырого диффа: память разбора растёт с числом строк, а не с байтами, и
# 5 МиБ односимвольных строк (~2,6 млн) дают пик ~640 МиБ при контейнере воркера 512 МБ.
# На 200 000 строк худший замер в контейнере — ~200 МиБ (design.md change
# `worker-diff-line-limit`, D3). Считаются строки всего диффа, вместе с файлами, которые
# потом отбросит фильтр (lock-файлы): разбирается дифф целиком.
# Значение — из памяти воркера, а не кабинетного `RUN_DIFF_MAX_LINES`. Константа, а не
# настройка: она держит память процесса и от стенда не зависит.
REVIEW_DIFF_MAX_LINES = 200_000


def fetch_review_diff(job: ReviewJob, vcs: VcsGateway, *, max_bytes: int) -> str:
    """Unified diff запроса на изменение между `base_sha` и `head_sha` задачи.

    Дифф больше `max_bytes` не дочитывается и не ревьюится по частям, а слишком
    большой для GitHub тот не строит (`422`): `DiffTooLargeError`, прогон уходит в
    `failed` с понятной причиной — в ней названы оба случая. Дифф длиннее
    `REVIEW_DIFF_MAX_LINES` строк отвергается так же, до разбора.
    """
    try:
        raw = vcs.fetch_diff(
            job.repository_full_name,
            job.base_sha,
            job.head_sha,
            job.installation_id,
            max_bytes=max_bytes,
        )
    except VcsFileTooLargeError as error:
        raise DiffTooLargeError(
            f"дифф PR больше лимита {max_bytes} байт или GitHub не строит такой большой дифф: ревью не выполняется"
        ) from error
    # Считаем строки до разбора: разбор и есть то, что не помещается в память.
    if raw.count("\n") > REVIEW_DIFF_MAX_LINES:
        raise DiffTooLargeError(f"дифф PR больше лимита {REVIEW_DIFF_MAX_LINES} строк: ревью не выполняется")
    return raw


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
        now=now,
    )
    sources, skipped = _fetch_sources(
        job, filter_diff_files(files, max_file_bytes=limits.diff_file_max_bytes), vcs, limits
    )
    asts, parse_failed = _collect_asts(job, sources, extractor)
    return assemble_context(
        job,
        files,
        rules,
        asts,
        skipped + parse_failed,
        sources=sources,
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


def _fetch_sources(
    job: ReviewJob, files: Sequence[ParsedFile], vcs: VcsGateway, limits: ContextLimits
) -> tuple[dict[str, str], list[SkippedAst]]:
    """Тексты изменённых файлов на `head_sha` - для AST и окон вокруг hunk'ов, в пределах лимитов.

    Удалённый, бинарный файл или файл без hunk'ов не довыбирается. Файлы с
    AST-языком идут первыми: файлы без него не вытесняют их из `ast_max_files`.
    Сверх лимита, отсутствующий, недоступный или слишком большой файл - без
    текста; в `SkippedAst` с причиной он попадает, только если у его языка есть
    AST: окно - необязательное улучшение, о нём `omitted` не пишет.
    """
    candidates = sorted(
        (f for f in files if not f.is_binary and not f.is_deleted and f.hunks),
        key=lambda f: language_for_path(f.file_path) is None,
    )
    skipped = [
        SkippedAst(f.file_path, "file_limit")
        for f in candidates[limits.ast_max_files :]
        if language_for_path(f.file_path) is not None
    ]
    sources: dict[str, str] = {}
    for parsed in candidates[: limits.ast_max_files]:
        path = parsed.file_path
        reason: str | None = None
        try:
            source = vcs.fetch_file(
                job.repository_full_name,
                path,
                job.head_sha,
                job.installation_id,
                max_bytes=limits.ast_max_file_bytes,
            )
            if source is None:
                reason = "missing"
            else:
                sources[path] = source
        except VcsFileTooLargeError:
            # Адаптер оборвал загрузку на лимите — тело в память не попало.
            reason = "too_large"
        except VcsError:
            logger.warning("файл %s@%s недоступен для контекста", path, job.head_sha, exc_info=True)
            reason = "unavailable"
        if reason is not None and language_for_path(path) is not None:
            skipped.append(SkippedAst(path, reason))
    return sources, skipped


def _collect_asts(
    job: ReviewJob, sources: Mapping[str, str], extractor: DefinitionExtractor
) -> tuple[list[FileAst], list[SkippedAst]]:
    """Контуры скачанных файлов с поддержанным языком; сломавший парсер - в `SkippedAst`."""
    asts: list[FileAst] = []
    skipped: list[SkippedAst] = []
    for path, source in sources.items():
        language = language_for_path(path)
        if language is None:
            continue
        try:
            outline = extractor.extract(source, language)
        except Exception:
            logger.exception("парсер не справился с %s@%s", path, job.head_sha)
            skipped.append(SkippedAst(path, "parse_error"))
            continue
        asts.append(FileAst(path, language, outline))
    return asts, skipped
