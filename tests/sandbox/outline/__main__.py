"""CLI песочницы tree-sitter: `uv run python -m tests.sandbox.outline`.

Гоняет настоящий этап сборки контекста (`fetch_review_diff` →
`build_review_context`) на фейковом GitHub и печатает по каждому файлу то, что
этап ответил: элементы блока `ast` пакета, записи `omitted` и охват; с
`--full-outline` - ещё и весь контур tree-sitter. Модели, базы, брокера и сети нет. Входы - кейсы
корпуса (`--case`), YAML-кейсы с ключом `lines:` (`--case-file`), диапазоны
git (`--from-git`) и отдельные файлы (`--file` + `--lines`); без входов -
`examples/outline-case.yaml`. Код выхода: 0 - всё разобрано; 1 - дифф PR не
получен или в видимом `omitted` есть `parse_error`, `missing`, `unavailable`;
2 - вход или настройки не подходят (отчёт не напечатан) либо не удалось
записать `--json` (уже после отчёта).
"""

import argparse
import dataclasses
import json
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.domain.untrusted import sanitize
from app.worker.factory import WorkerConfigError

from ...eval.rules.corpus import CONFIGS, load_corpus
from ..inputs import InputError, PrInput, from_case, from_git
from ..worker import SandboxConfig
from .runner import FileOutlineReport, PrOutline, run_outline
from .sources import from_file, load_outline_case

EXAMPLE = Path(__file__).parent / "examples" / "outline-case.yaml"

# Причины пропуска AST, которых на фейковом GitHub быть не должно: это ошибка
# самой песочницы, а не свойство файла.
_BROKEN_REASONS = frozenset({"parse_error", "missing", "unavailable"})
# Части `omitted`, относящиеся к AST файла.
_AST_PARTS = frozenset({"ast", "scopes", "imports"})


def _parse_args(
    argv: Sequence[str] | None, case_ids: Sequence[str]
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tests.sandbox.outline",
        description="Песочница tree-sitter: что этап сборки контекста отдаёт модели из AST.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        choices=case_ids,
        metavar="ID",
        help="id кейса корпуса eval; можно повторять: " + ", ".join(case_ids),
    )
    parser.add_argument(
        "--case-file",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="YAML-кейс в формате корпуса, правки - ключом lines:; можно повторять",
    )
    parser.add_argument(
        "--from-git",
        action="append",
        default=[],
        metavar="RANGE",
        help="PR из диапазона BASE..HEAD репозитория --repo; можно повторять",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        metavar="DIR",
        help="репозиторий для --from-git (по умолчанию текущий каталог)",
    )
    parser.add_argument(
        "--file",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="PR из одного файла; можно повторять",
    )
    parser.add_argument(
        "--lines",
        metavar="SPEC",
        help="изменённые строки единственного --file: N, N-M или список через запятую;"
        " без него файл новый и затронут целиком",
    )
    parser.add_argument(
        "--rules",
        choices=tuple(CONFIGS),
        default="rules",
        help="что лежит в .review/rules.md целевой ветки (часть бюджета пакета)",
    )
    parser.add_argument(
        "--context-max-tokens",
        type=int,
        help="потолок пакета контекста (CONTEXT_MAX_TOKENS)",
    )
    parser.add_argument(
        "--full-outline",
        action="store_true",
        help="печатать ещё и весь контур файла (отдельный разбор tree-sitter)",
    )
    parser.add_argument(
        "--json", type=Path, metavar="PATH", help="записать результат в JSON"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="логи уровня INFO")
    return parser.parse_args(argv)


def _collect_inputs(args: argparse.Namespace) -> list[PrInput]:
    """PR в порядке --case, --case-file, --from-git, --file; без входов - пример."""
    if args.lines is not None and len(args.file) != 1:
        raise InputError("--lines работает ровно с одним --file")
    by_id = {case.id: case for case in load_corpus()} if args.case else {}
    prs = [from_case(by_id[i]) for i in args.case]
    prs += [load_outline_case(path) for path in args.case_file]
    prs += [from_git(spec, args.repo) for spec in args.from_git]
    prs += [from_file(path, args.lines) for path in args.file]
    return prs or [load_outline_case(EXAMPLE)]


def compress_lines(lines: Sequence[int]) -> str:
    """Номера строк диапазонами: `(3, 4, 5, 12)` → `3-5, 12`; пусто - `-`."""
    parts: list[str] = []
    numbers = sorted(lines)
    start = 0
    for i in range(1, len(numbers) + 1):
        if i == len(numbers) or numbers[i] != numbers[i - 1] + 1:
            first, last = numbers[start], numbers[i - 1]
            parts.append(str(first) if first == last else f"{first}-{last}")
            start = i
    return ", ".join(parts) or "-"


def _indented(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.split("\n"))


def _problems(pr: PrOutline) -> list[str]:
    """Проблемы PR: дифф не получен, в видимом `omitted` - сбой загрузки или разбора,
    или упал разбор `--full-outline`, а урезанный `omitted` сбой не показал."""
    problems = [f"дифф не получен: {pr.error}"] if pr.error else []
    broken = [
        entry
        for entry in pr.omitted
        if entry["part"] == "ast" and entry["reason"] in _BROKEN_REASONS
    ]
    problems += [f"{entry['path']}: {entry['reason']}" for entry in broken]
    shown = {entry["path"] for entry in broken}
    problems += [
        f"{sanitize(file.path)}: разбор упал: {file.outline.error}"
        for file in pr.files
        if file.outline is not None
        and file.outline.error is not None
        and sanitize(file.path) not in shown
    ]
    return problems


def _omission(entry: dict[str, Any]) -> str:
    """Запись `omitted` коротко: `scopes - budget`, `hunk:7 - budget`, `commit_message #2 - budget`."""
    where = f":{entry['line']}" if "line" in entry else ""
    index = f" #{entry['index']}" if "index" in entry else ""
    path = f"{entry['path']} " if "path" in entry else ""
    return f"{path}{entry['part']}{where}{index} - {entry['reason']}"


def _print_file(file: FileOutlineReport, *, truncated: bool) -> None:
    language = f"  {file.language}" if file.language else ""
    print(
        f"{file.path}{language}  изменены строки {compress_lines(file.touched)}"
        f"  охват: {file.coverage or '-'}"
    )
    for item in file.ast:
        part = "scopes" if "scopes" in item else "imports"
        print(f"  ast/{part} (как видит модель):")
        print(_indented(item[part]))
    for entry in file.omitted:
        print(f"  omitted: {_omission(entry).removeprefix(entry['path'] + ' ')}")
    if not file.ast and not any(e["part"] in _AST_PARTS for e in file.omitted):
        print(
            "  AST в пакете нет; причина могла не войти в урезанный omitted"
            if truncated
            else "  AST не выбирался"
        )
    if file.outline is not None and file.outline.error is not None:
        print(f"  весь контур: разбор упал: {file.outline.error}")
    elif file.outline is not None:
        outline = file.outline
        print(
            f"  весь контур: определений {len(outline.definitions)},"
            f" импортов {len(outline.imports)}"
        )
        print(_indented("\n".join((*outline.definitions, *outline.imports)) or "-"))
        if not outline.definitions and not outline.imports:
            print(
                "  предупреждение: пустой контур - нормально для __init__.py или модуля"
                " констант, на файле с определениями - сломан запрос грамматики"
            )


def _print_pr(pr: PrOutline, problems: list[str]) -> None:
    print(f"=== [{pr.number}] {pr.id}: {pr.title}")
    truncated = pr.omitted_count > len(pr.omitted)
    for file in pr.files:
        _print_file(file, truncated=truncated)
    paths = {sanitize(file.path) for file in pr.files}
    other = [e for e in pr.omitted if e.get("path") not in paths]
    if other:
        print("omitted вне файлов диффа: " + ", ".join(_omission(e) for e in other))
    if pr.error is None:
        ast_files = len({item["path"] for item in pr.ast})
        print(
            f"пакет: файлов с AST: {ast_files}, ~{pr.ast_tokens} токенов;"
            f" весь пакет ~{pr.token_count} из {pr.max_tokens};"
            f" omitted: {len(pr.omitted)} из {pr.omitted_count};"
            f" уровни: {', '.join(pr.tiers)}"
        )
        if truncated:
            print(
                "  перечень omitted урезан бюджетом: у части файлов причина не видна"
                " (модель её тоже не видит)"
            )
    print("проблемы: " + ("; ".join(problems) if problems else "-"))


def _settings_error(error: ValidationError) -> str:
    """Ошибка настроек одной строкой: поле и причина."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors()
    )


def main(argv: Sequence[str] | None = None) -> int:
    case_ids = tuple(case.id for case in load_corpus())
    args = _parse_args(argv, case_ids)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = SandboxConfig(
        llm="stub",
        rules_config=args.rules,
        context_max_tokens=args.context_max_tokens,
    )
    started = time.monotonic()
    try:
        prs = _collect_inputs(args)
        result = run_outline(prs, config, full_outline=args.full_outline)
    except InputError as error:
        print(error, file=sys.stderr)
        return 2
    except ValidationError as error:
        print(f"настройки не подходят: {_settings_error(error)}", file=sys.stderr)
        return 2
    except WorkerConfigError as error:
        flat = " ".join(str(error).split())
        print(f"воркер не стартует с таким окном: {flat}", file=sys.stderr)
        return 2
    elapsed = time.monotonic() - started

    problems = {pr.number: _problems(pr) for pr in result}
    max_tokens = result[0].max_tokens if result else 0
    print(
        f"песочница tree-sitter: PR {len(result)}, правила: {args.rules},"
        f" лимит пакета: {max_tokens} токенов"
    )
    for pr in result:
        _print_pr(pr, problems[pr.number])
    with_ast = sum(len({item["path"] for item in pr.ast}) for pr in result)
    total = sum(len(p) for p in problems.values())
    print(
        f"итого: PR {len(result)}, файлов с AST {with_ast}, проблем {total},"
        f" время {elapsed:.1f} с"
    )
    if args.json is not None:
        try:
            args.json.write_text(
                json.dumps(
                    [dataclasses.asdict(pr) for pr in result], ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
        except OSError as error:
            print(f"результат не записан в {args.json}: {error}", file=sys.stderr)
            return 2
        print(f"результат: {args.json}")
    return 1 if any(problems.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
