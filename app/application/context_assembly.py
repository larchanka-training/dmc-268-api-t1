"""Сборка пакета контекста прогона — чистая функция над уже полученными данными.

На вход — разобранный дифф, правила владельца и контуры файлов, на выход —
`ContextPayload` с телом `context-payload/v1` и hunk'и, которые модель
увидела (по ним `validate_anchor` сверяет находки). Сеть, кэш и парсер —
забота `use_cases.build_review_context`; здесь только решения: что войдёт,
в каком порядке и как отделено от инструкций.

Жёсткий лимит (design D5). Бюджет — `max_tokens × 4` байт сериализованного
тела; каркас оплачивается заранее, каждый элемент массива — своей длиной.
Порядок: первым - контекст PR (заголовок, описание, сообщения коммитов от
нового к старому) в своей доле, неистраченное возвращается в общий бюджет
(change `add-llm-gateway`, design D6); затем под правила резервируется то,
что им нужно на файлах диффа (не больше их доли), затем hunk'и всех файлов
в порядке диффа, затем правила —
уже по файлам, чей дифф вошёл, — затем
AST: сначала охватывающие определения всех файлов, потом импорты. Элемент,
не поместившийся целиком, не обрезается, а перечисляется в `omitted`;
под этот перечень заранее отложен резерв, остаток перечня считает
`omitted_count`.

Недоверенные данные (design D6). Каждый текст из репозитория — hunk,
сигнатуры, импорты, проверки правил, текст автора PR — лежит внутри
`fence`; пути и имена — отдельными полями, обезвреженными `sanitize`.
"""

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.code_context import select_scopes, touched_lines
from app.domain.context_budget import (
    BYTES_PER_TOKEN,
    Budget,
    estimate_tokens,
    json_size,
    serialize,
)
from app.domain.diff_parser import filter_diff_files
from app.domain.entities import (
    ContextPayload,
    Definition,
    FileOutline,
    Hunk,
    ParsedFile,
    RepoRules,
    ReviewJob,
    ShownRule,
)
from app.domain.enums import Language
from app.domain.repo_rules import RULES_PATH, applicable_rules
from app.domain.untrusted import fence, sanitize

SCHEMA = "context-payload/v1"

# Под перечень не поместившегося: десятая доля лимита, но не больше 4 КиБ.
_OMITTED_RESERVE_MAX_BYTES = 4096
# Заглушка счётчика в каркасе: не короче любого реального значения.
_COUNT_PLACEHOLDER = 10**12


@dataclass(frozen=True, slots=True)
class ContextLimits:
    """Лимиты сборки из конфигурации; приходят аргументом, а не из окружения."""

    max_tokens: int
    rules_max_tokens: int
    rules_max_bytes: int
    rules_cache_ttl_seconds: int
    ast_max_files: int
    ast_max_file_bytes: int
    pr_context_max_tokens: int


@dataclass(frozen=True, slots=True)
class PrContext:
    """Текст автора PR для пакета; `None` - VCS не смогла его вернуть.

    Пустая строка или пустой кортеж - текста нет (PR без описания), это не
    сбой и в `omitted` не попадает. Заголовок и описание приходят одним
    запросом, поэтому недоступны вместе.
    """

    title: str | None
    description: str | None
    # От нового к старому: при нехватке доли теряются старые коммиты.
    commit_messages: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class FileAst:
    """Контур файла диффа, полученный парсером на `head_sha`."""

    path: str
    language: Language
    outline: FileOutline


@dataclass(frozen=True, slots=True)
class SkippedAst:
    """Файл, для которого AST-окружение не строилось, и почему."""

    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class AssembledContext:
    payload: ContextPayload
    # Hunk'и, вошедшие в пакет: находка вне них указывает на то, чего модель
    # не видела.
    hunks: tuple[Hunk, ...]
    # Правила, вошедшие в пакет, - по тому же принципу для ссылки на правило.
    rules: tuple[ShownRule, ...] = ()


def assemble_context(
    job: ReviewJob,
    files: Sequence[ParsedFile],
    rules: RepoRules,
    asts: Iterable[FileAst],
    skipped_asts: Iterable[SkippedAst],
    *,
    pr_context: PrContext,
    limits: ContextLimits,
    review_run_id: UUID,
    now: datetime,
    new_id: Callable[[], UUID],
) -> AssembledContext:
    reviewable = filter_diff_files(files)
    reviewable_paths = {f.file_path for f in reviewable}
    omitted: list[dict[str, Any]] = [
        _omission(f.file_path, "file", "filtered")
        for f in files
        if f.file_path not in reviewable_paths
    ]
    omitted += [_omission(s.path, "ast", s.reason) for s in skipped_asts]

    body: dict[str, Any] = {
        "schema": SCHEMA,
        "repository": sanitize(job.repository_full_name),
        "pull_request": job.pull_request_number,
        "base_sha": job.base_sha,
        "head_sha": job.head_sha,
        "limits": {"max_tokens": limits.max_tokens},
        "pull_request_context": [],
        "rules": {"status": rules.status.value, "source": RULES_PATH, "items": [], "rejected": []},
        "diff": [],
        "ast": [],
        "omitted": [],
        "omitted_count": _COUNT_PLACEHOLDER,
    }
    limit_bytes = limits.max_tokens * BYTES_PER_TOKEN
    reserve = min(limit_bytes // 10, _OMITTED_RESERVE_MAX_BYTES)
    budget = Budget(limit_bytes - json_size(body) - reserve)

    pr_items, unavailable = _pr_context_items(pr_context)
    omitted += unavailable
    pr_share = budget.split(
        min(limits.pr_context_max_tokens * BYTES_PER_TOKEN, sum(Budget.cost(i) for i, _ in pr_items))
    )
    for item, omission in pr_items:
        if pr_share.take(item):
            body["pull_request_context"].append(item)
        else:
            omitted.append(omission)
    budget.merge(pr_share)

    # Правила резервируют не всю свою долю, а сколько им нужно на всех
    # файлах диффа: без правил дифф получает весь бюджет. После диффа правила
    # привязываются к вошедшим файлам — подмножеству, — и в резерв укладываются.
    candidate_paths = tuple(dict.fromkeys(h.file_path for f in reviewable for h in f.hunks))
    wanted = sum(Budget.cost(item) for item, _ in _rule_items(rules, candidate_paths))
    rules_share = budget.split(min(limits.rules_max_tokens * BYTES_PER_TOKEN, wanted))

    shown: list[Hunk] = []
    for parsed in reviewable:
        for hunk in parsed.hunks:
            item = _hunk_item(parsed, hunk)
            if budget.take(item):
                body["diff"].append(item)
                shown.append(hunk)
            else:
                omitted.append(_omission(parsed.file_path, "hunk", "budget", line=hunk.new_start))
    file_paths = tuple(dict.fromkeys(h.file_path for h in shown))

    shown_rules: list[ShownRule] = []
    for item, rule in _rule_items(rules, file_paths):
        section = "items" if rule is not None else "rejected"
        if rules_share.take(item):
            body["rules"][section].append(item)
            if rule is not None:
                shown_rules.append(rule)
        elif section == "items":
            omitted.append(_omission(RULES_PATH, "rule", "budget", name=item["name"]))
        else:
            omitted.append(_omission(RULES_PATH, "rejected_rule", "budget"))
    budget.merge(rules_share)

    shown_by_file: dict[str, list[Hunk]] = {}
    for hunk in shown:
        shown_by_file.setdefault(hunk.file_path, []).append(hunk)
    ast_files = [a for a in asts if a.path in shown_by_file]
    for file_ast in ast_files:
        scopes = select_scopes(file_ast.outline, touched_lines(shown_by_file[file_ast.path]))
        if scopes:
            _take_ast(budget, body, omitted, file_ast, "scopes", _render_scopes(scopes))
    for file_ast in ast_files:
        if file_ast.outline.imports:
            _take_ast(budget, body, omitted, file_ast, "imports", "\n".join(file_ast.outline.imports))

    # Перечень не поместившегося — в остаток бюджета плюс отложенный резерв.
    budget.remaining += reserve
    body["omitted"] = [entry for entry in omitted if budget.take(entry)]
    body["omitted_count"] = len(omitted)

    serialized = serialize(body)
    return AssembledContext(
        payload=ContextPayload(
            id=new_id(),
            review_run_id=review_run_id,
            chunk_index=0,
            tiers=("diff", "ast") if body["ast"] else ("diff",),
            file_paths=file_paths,
            token_count=estimate_tokens(serialized),
            content_sha256=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            body=body,
            created_at=now,
            updated_at=now,
        ),
        hunks=tuple(shown),
        rules=tuple(shown_rules),
    )


def _rule_items(
    rules: RepoRules, paths: Sequence[str]
) -> list[tuple[dict[str, Any], ShownRule | None]]:
    """Применимые к `paths` правила, затем отклонённые - элементы раздела `rules`.

    У применимого правила рядом с элементом пакета - `ShownRule` для сверки
    ссылок; у отклонённого - `None`.
    """
    items: list[tuple[dict[str, Any], ShownRule | None]] = [
        (
            {
                "name": sanitize(rule.name),
                "files": [sanitize(p) for p in rule_files],
                "checks": [fence("rule_check", check) for check in rule.checks],
            },
            ShownRule(
                name=sanitize(rule.name),
                files=rule_files,
                checks=tuple(sanitize(check) for check in rule.checks),
            ),
        )
        for rule, rule_files in applicable_rules(rules.rules, paths)
    ]
    items += [({"name": sanitize(r.name), "reason": r.reason}, None) for r in rules.rejected]
    return items


def _pr_context_items(
    context: PrContext,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]]]:
    """Элементы раздела `pull_request_context` с записью `omitted` на случай
    нехватки доли - и записи о том, что VCS вернуть не смогла.

    У текста автора PR нет пути, поэтому записи `omitted` без поля `path`.
    """
    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unavailable: list[dict[str, Any]] = []
    if context.title is None or context.description is None:
        unavailable.append({"part": "pr_metadata", "reason": "unavailable"})
    else:
        for part, kind, text in (
            ("title", "pr_title", context.title),
            ("description", "pr_description", context.description),
        ):
            if text:
                items.append(
                    ({"part": part, "text": fence(kind, text)}, {"part": kind, "reason": "budget"})
                )
    if context.commit_messages is None:
        unavailable.append({"part": "commit_messages", "reason": "unavailable"})
    else:
        for index, message in enumerate(context.commit_messages, start=1):
            items.append(
                (
                    {"part": "commit", "text": fence("commit_message", message)},
                    {"part": "commit_message", "reason": "budget", "index": index},
                )
            )
    return items, unavailable


def _hunk_item(parsed: ParsedFile, hunk: Hunk) -> dict[str, Any]:
    item: dict[str, Any] = {"path": sanitize(parsed.file_path), "hunk": fence("diff", hunk.text)}
    if parsed.old_path is not None:
        item["old_path"] = sanitize(parsed.old_path)
    return item


def _take_ast(
    budget: Budget,
    body: dict[str, Any],
    omitted: list[dict[str, Any]],
    file_ast: FileAst,
    part: str,
    text: str,
) -> None:
    item = {"path": sanitize(file_ast.path), "language": file_ast.language.value, part: fence(part, text)}
    if budget.take(item):
        body["ast"].append(item)
    else:
        omitted.append(_omission(file_ast.path, part, "budget"))


def _render_scopes(scopes: Sequence[Definition]) -> str:
    """Строки `начало-конец сигнатура`, вложенные определения — с отступом."""
    names = {d.name for d in scopes}
    lines = []
    for definition in scopes:
        indent = "  " if definition.parent in names else ""
        lines.append(f"{indent}{definition.line}-{definition.end_line} {definition.signature}")
    return "\n".join(lines)


def _omission(path: str, part: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"path": sanitize(path), "part": part, "reason": reason, **extra}
