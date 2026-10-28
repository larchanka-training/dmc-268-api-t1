"""Прогон песочницы контура: настоящий путь `fetch_review_diff` → `build_review_context`.

Модели нет: этап сборки контекста запускается на фейковом GitHub, а отчёт
показывает, что tree-sitter нашёл в файле, что из этого ушло в блок `ast`
пакета и что отброшено. Печати здесь нет - её делает `__main__`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.application.ports.definition_extractor import DefinitionExtractor
from app.application.use_cases.build_review_context import (
    build_review_context,
    fetch_review_diff,
)
from app.domain.code_context import language_for_path, select_scopes, touched_lines
from app.domain.context_budget import estimate_tokens, serialize
from app.domain.diff_parser import is_filterable, parse_diff
from app.domain.entities import Definition, FileOutline, Hunk, ParsedFile
from app.domain.enums import Language
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


@dataclass(frozen=True, slots=True)
class FileOutlineReport:
    """Один файл диффа глазами этапа сборки контекста.

    `status`: `in_context` - в пакет вошёл хоть один элемент AST; `budget` -
    элементы AST отброшены бюджетом; `no_scopes` - разобран, но охватывающих
    определений и импортов нет; `diff_budget` - hunk'и файла не вошли, AST не
    выбирался; `skipped` - AST пропущен (`reason`, если запись осталась в
    урезанном перечне `omitted`); `no_hunks` - в диффе нет hunk'ов (пустой
    новый файл); `no_grammar`, `deleted`, `filtered`.
    """

    path: str
    language: str | None
    status: str
    reason: str | None
    touched: tuple[int, ...]
    # Размер контура; `None` - файл не разбирался.
    definitions: int | None
    imports: int | None
    # Весь контур строками «начало-конец сигнатура» (для --full-outline).
    outline: tuple[str, ...]
    # Тексты элементов `scopes`/`imports` из пакета, без обёртки `fence`.
    scopes: str | None
    import_text: str | None
    # Части AST, отброшенные бюджетом: "scopes", "imports".
    dropped: tuple[str, ...]
    # Разобран, код непустой, а контур пуст.
    empty_outline: bool


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
    omitted: tuple[dict[str, Any], ...]
    omitted_count: int
    ast: tuple[dict[str, Any], ...]
    # Дифф не получен (`DiffTooLargeError`).
    error: str | None = None


@dataclass(frozen=True, slots=True)
class OutlineResult:
    prs: list[PrOutline] = field(default_factory=list)


class _RecordingExtractor:
    """Настоящий экстрактор, запоминающий контур каждого разобранного файла.

    Так отчёт видит контур целиком без второго разбора и без приватной
    `_collect_asts`; падение парсера (`parse_error`) - тоже, не полагаясь на
    урезанный бюджетом `omitted`. Порт `DefinitionExtractor` реализует структурно.
    """

    def __init__(self, inner: DefinitionExtractor) -> None:
        self._inner = inner
        self.outlines: dict[tuple[str, Language], FileOutline] = {}
        self.failed: set[tuple[str, Language]] = set()

    def extract(self, source: str, language: Language) -> FileOutline:
        try:
            outline = self._inner.extract(source, language)
        except Exception:
            self.failed.add((source, language))
            raise
        self.outlines[(source, language)] = outline
        return outline


def _unfence(fenced: str) -> str:
    """Текст из блока `fence`: без первой (`<<<untrusted …>>>`) и последней строки."""
    return "\n".join(fenced.split("\n")[1:-1])


def _render_outline(definitions: Sequence[Definition]) -> tuple[str, ...]:
    """Весь контур как `_render_scopes` пакета: вложенные - с отступом 2 пробела."""
    names = {d.name for d in definitions}
    return tuple(
        f"{'  ' if d.parent in names else ''}{d.line}-{d.end_line} {d.signature}"
        for d in definitions
    )


def _file_report(
    parsed: ParsedFile,
    head: str | None,
    *,
    shown_hunks: Sequence[Hunk],
    ast: Sequence[dict[str, Any]],
    omitted: Sequence[dict[str, Any]],
    extractor: _RecordingExtractor,
) -> FileOutlineReport:
    path = parsed.file_path
    clean = sanitize(path)
    language = language_for_path(path)
    items = [item for item in ast if item["path"] == clean]
    key = (head, language) if head is not None and language else None
    outline = extractor.outlines.get(key) if key else None
    # Перечень `omitted` урезан бюджетом: из него берётся только причина пропуска,
    # а пропуск, `parse_error` и отброшенное бюджетом выводятся из исходных данных.
    reason = (
        "parse_error"
        if key in extractor.failed
        else next(
            (
                e["reason"]
                for e in omitted
                if e.get("path") == clean and e["part"] == "ast"
            ),
            None,
        )
    )
    shown = [hunk for hunk in shown_hunks if hunk.file_path == path]
    dropped: tuple[str, ...] = ()
    if shown and outline is not None:
        want = (("scopes",) if select_scopes(outline, touched_lines(shown)) else ()) + (
            ("imports",) if outline.imports else ()
        )
        dropped = tuple(
            part for part in want if not any(part in item for item in items)
        )
    scopes = next((_unfence(i["scopes"]) for i in items if "scopes" in i), None)
    import_text = next((_unfence(i["imports"]) for i in items if "imports" in i), None)

    if parsed.is_deleted:
        status = "deleted"
    elif is_filterable(path, parsed.is_binary):
        status = "filtered"
    elif language is None:
        status = "no_grammar"
    elif not parsed.hunks:
        status = "no_hunks"
    elif outline is None:
        # Кандидат на AST, как в `_collect_asts`: без контура - значит пропущен.
        status = "skipped"
    elif not shown:
        status = "diff_budget"
    elif items:
        status = "in_context"
    elif dropped:
        status = "budget"
    else:
        status = "no_scopes"

    return FileOutlineReport(
        path=path,
        language=language.value if language else None,
        status=status,
        reason=reason if status == "skipped" else None,
        touched=tuple(sorted(touched_lines(parsed.hunks))),
        definitions=len(outline.definitions) if outline else None,
        imports=len(outline.imports) if outline else None,
        outline=_render_outline(outline.definitions) if outline else (),
        scopes=scopes,
        import_text=import_text,
        dropped=dropped,
        empty_outline=(
            outline is not None
            and head is not None
            and bool(head.strip())
            and not outline.definitions
            and not outline.imports
        ),
    )


def run_outline(
    prs: Sequence[PrInput],
    config: SandboxConfig,
    *,
    extractor_factory: Callable[
        [], DefinitionExtractor
    ] = TreeSitterDefinitionExtractor,
) -> OutlineResult:
    """Собрать контекст каждого PR, как воркер, и описать, что в нём от AST.

    `WorkerConfigError` (окно) и `pydantic.ValidationError` (настройки)
    выходят наружу до первого PR, как у воркера. `extractor_factory` - для
    тестов: экстрактор, который падает, даёт `parse_error`.
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

    result = OutlineResult()
    for n, pr in enumerate(prs, start=1):
        job = seed_case(pr, n, github, FakeUnitOfWork(), config.rules_config)
        # Свой на каждый PR: файл с тем же текстом не наследует чужой контур.
        extractor = _RecordingExtractor(extractor_factory())
        try:
            diff = fetch_review_diff(job, vcs, max_bytes=limits.diff_max_bytes)
        except DiffTooLargeError as error:
            result.prs.append(
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
        files = tuple(
            _file_report(
                parsed,
                pr.head_files.get(parsed.file_path),
                shown_hunks=assembled.hunks,
                ast=ast,
                omitted=omitted,
                extractor=extractor,
            )
            for parsed in parse_diff(diff)
        )
        result.prs.append(
            PrOutline(
                id=pr.id,
                title=pr.title,
                number=n,
                files=files,
                token_count=assembled.payload.token_count,
                max_tokens=limits.max_tokens,
                ast_tokens=estimate_tokens(serialize(ast)),
                omitted=tuple(omitted),
                omitted_count=body["omitted_count"],
                ast=tuple(ast),
                error=None,
            )
        )
    return result
