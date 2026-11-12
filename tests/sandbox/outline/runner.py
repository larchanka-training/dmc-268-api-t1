"""Прогон песочницы контура: настоящий путь `fetch_review_diff` → `build_review_context`.

Модели нет: этап сборки контекста запускается на фейковом GitHub. Отчёт по файлу
собирается только из ответа этапа - блок `ast` пакета, записи `omitted` и охват
`coverage`; своих правил выбора песочница не держит, поэтому не расходится с
приложением. Печати здесь нет - её делает `__main__`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.application.use_cases.build_review_context import (
    build_review_context,
    fetch_review_diff,
)
from app.domain.code_context import language_for_path, touched_lines
from app.domain.context_budget import estimate_tokens, serialize
from app.domain.diff_parser import parse_diff
from app.domain.review_coverage import ReviewCoverage
from app.domain.untrusted import sanitize
from app.domain.vcs_errors import DiffTooLargeError
from app.infrastructure.container import WORKER_VCS_LIMITS
from app.infrastructure.parsing.tree_sitter_extractor import (
    TreeSitterDefinitionExtractor,
)
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.worker.factory import context_limits, load_checked_prompt_texts

from ...fakes.context import FakeCache
from ...fakes.github_api import (
    APP_ID,
    INSTALLATION_ID,
    REPO_FULL_NAME,
    FakeGitHub,
    app_private_key,
)
from ...fakes.unit_of_work import FakeUnitOfWork
from ..inputs import PrInput
from ..worker import SandboxConfig, build_settings, seed_case
from .sources import OutlinePr


@dataclass(frozen=True, slots=True)
class FullOutline:
    """Весь контур файла (`--full-outline`): строки «начало-конец сигнатура» и импорты.

    `error` - имя типа исключения, если разбор упал; тогда контур пуст.
    """

    definitions: tuple[str, ...]
    imports: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class FileOutlineReport:
    """Один файл диффа - только то, что о нём ответил этап сборки контекста.

    `coverage` - группа файла в `AssembledContext.coverage` (`reviewed`,
    `partial`, `not_reviewed`, `filtered`); `None` - файла в охвате нет (нет
    hunk'ов). `ast` - элементы блока `ast` пакета этого файла, текст - как его
    видит модель; `omitted` - видимые записи `omitted` этого файла. Нет ни
    элементов `ast`, ни записей о них в `omitted` - AST для файла не выбирался.
    `outline` - весь контур при `--full-outline`; `None` - не разбирался.
    """

    path: str
    language: str | None
    touched: tuple[int, ...]
    coverage: str | None
    ast: tuple[dict[str, Any], ...]
    omitted: tuple[dict[str, Any], ...]
    outline: FullOutline | None = None


@dataclass(frozen=True, slots=True)
class PrOutline:
    """Итог сборки контекста одного PR."""

    id: str
    title: str
    number: int
    files: tuple[FileOutlineReport, ...]
    # Весь пакет и его лимит, в токенах.
    token_count: int
    max_tokens: int
    # Блок `ast` пакета, в токенах.
    ast_tokens: int
    # Перечень `omitted` урезается бюджетом: `omitted_count` - сколько записей было.
    omitted: tuple[dict[str, Any], ...]
    omitted_count: int
    ast: tuple[dict[str, Any], ...]
    # Уровни пакета (`payload.tiers`): `whole_file` - часть файлов вошла в дифф
    # целиком, AST для них не выбирался.
    tiers: tuple[str, ...] = ()
    # Дифф не получен (`DiffTooLargeError`).
    error: str | None = None


def _coverage_groups(coverage: ReviewCoverage) -> dict[str, str]:
    return {
        path: group
        for group in ("reviewed", "partial", "not_reviewed", "filtered")
        for path in getattr(coverage, group)
    }


def _full_outline(
    extractor: TreeSitterDefinitionExtractor, path: str, head: str | None
) -> FullOutline | None:
    """Один вызов публичного `extract` на тексте head; без языка или текста - `None`."""
    language = language_for_path(path)
    if language is None or head is None:
        return None
    try:
        outline = extractor.extract(head, language)
    # Отдельный разбор: этап разбирает не все файлы (отфильтрованные, сверх лимита),
    # поэтому падение здесь не обязательно есть в omitted - показываем его сами.
    except Exception as error:  # noqa: BLE001 — сбой разбора - строка отчёта, а не traceback
        return FullOutline(definitions=(), imports=(), error=type(error).__name__)
    return FullOutline(
        definitions=tuple(f"{d.line}-{d.end_line} {d.signature}" for d in outline.definitions),
        imports=tuple(outline.imports),
    )


def run_outline(
    prs: Sequence[PrInput], config: SandboxConfig, *, full_outline: bool = False
) -> list[PrOutline]:
    """Собрать контекст каждого PR, как воркер, и разложить ответ этапа по файлам.

    `WorkerConfigError` (окно) и `pydantic.ValidationError` (настройки)
    выходят наружу до первого PR, как у воркера.
    """
    private_key = app_private_key()
    settings = build_settings(config, private_key, external_llm=True)
    # Паритет с воркером: с непригодным окном он не стартует.
    load_checked_prompt_texts(settings, DEFAULT_PROMPTS_DIR)
    limits = context_limits(settings)

    github = FakeGitHub(REPO_FULL_NAME, INSTALLATION_ID, private_key)
    client = github.client()
    client.timeout = WORKER_VCS_LIMITS.timeout
    auth = GitHubAppAuth(APP_ID, private_key, client)
    vcs = GitHubVcsGateway(
        auth,
        client,
        sleep=lambda _: None,
        rate_limit_budget_seconds=WORKER_VCS_LIMITS.rate_limit_budget_seconds,
    )
    extractor = TreeSitterDefinitionExtractor()

    result: list[PrOutline] = []
    for n, pr in enumerate(prs, start=1):
        job = seed_case(pr, n, github, FakeUnitOfWork(), config.rules_config)
        if isinstance(pr, OutlinePr) and pr.diffs:
            # Дифф по номерам строк вместо `difflib` фейкового GitHub (`sources`).
            github.pulls[n].diff = pr.pull_diff()
        try:
            diff = fetch_review_diff(job, vcs, max_bytes=limits.diff_max_bytes)
        except DiffTooLargeError as error:
            result.append(
                PrOutline(
                    id=pr.id,
                    title=pr.title,
                    number=n,
                    files=(),
                    token_count=0,
                    max_tokens=limits.max_tokens,
                    ast_tokens=0,
                    omitted=(),
                    omitted_count=0,
                    ast=(),
                    error=str(error),
                )
            )
            continue
        assembled = build_review_context(
            job,
            diff,
            vcs=vcs,
            extractor=extractor,
            cache=FakeCache(),
            limits=limits,
            review_run_id=job.review_run_id,
            now=datetime.now(UTC),
            new_id=uuid4,
        )
        body = assembled.payload.body
        ast: list[dict[str, Any]] = body["ast"]
        omitted: list[dict[str, Any]] = body["omitted"]
        groups = _coverage_groups(assembled.coverage)
        files = []
        for parsed in parse_diff(diff):
            path = parsed.file_path
            # Пакет хранит пути обезвреженными: сравниваем с тем же видом.
            clean = sanitize(path)
            language = language_for_path(path)
            files.append(
                FileOutlineReport(
                    path=path,
                    language=language.value if language else None,
                    touched=tuple(sorted(touched_lines(parsed.hunks))),
                    coverage=groups.get(path),
                    ast=tuple(item for item in ast if item["path"] == clean),
                    omitted=tuple(e for e in omitted if e.get("path") == clean),
                    outline=(
                        _full_outline(extractor, path, pr.head_files.get(path))
                        if full_outline
                        else None
                    ),
                )
            )
        result.append(
            PrOutline(
                id=pr.id,
                title=pr.title,
                number=n,
                files=tuple(files),
                token_count=assembled.payload.token_count,
                max_tokens=limits.max_tokens,
                ast_tokens=estimate_tokens(serialize(ast)) if ast else 0,
                omitted=tuple(omitted),
                omitted_count=body["omitted_count"],
                ast=tuple(ast),
                tiers=assembled.payload.tiers,
                error=None,
            )
        )
    return result
