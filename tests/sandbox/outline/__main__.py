"""CLI песочницы tree-sitter: `uv run python -m tests.sandbox.outline`.

Гоняет настоящий этап сборки контекста (`fetch_review_diff` →
`build_review_context`) на фейковом GitHub и печатает, что tree-sitter нашёл в
каждом файле, что выбрано вокруг изменённых строк, что ушло в блок `ast`
пакета и что отброшено. Модели, базы, брокера и сети нет. Входы - кейсы
корпуса (`--case`), YAML-кейсы с ключом `lines:` (`--case-file`), диапазоны
git (`--from-git`) и отдельные файлы (`--file` + `--lines`); без входов -
`examples/outline-case.yaml`. Код выхода: 0 - всё разобрано (пустой контур -
только предупреждение); 1 - дифф PR не получен, `parse_error` или файл не
загружен (`missing`, `unavailable`); 2 - вход или настройки не подходят, отчёт
не записан.
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

from app.worker.factory import WorkerConfigError

from ...eval.rules.corpus import CONFIGS, load_corpus
from ..inputs import InputError, PrInput, from_case, from_git
from ..worker import SandboxConfig
from .runner import FileOutlineReport, OutlineResult, PrOutline, run_outline
from .sources import from_file, load_outline_case

EXAMPLE = Path(__file__).parent / "examples" / "outline-case.yaml"
# Сколько записей «отброшено по бюджету» печатать, остальное - числом.
DROPPED_LIMIT = 10

# Причины пропуска AST, которых на фейковом GitHub быть не должно: это ошибка
# самой песочницы, а не свойство файла.
_BROKEN_REASONS = frozenset({"parse_error", "missing", "unavailable"})
# Однострочные описания статусов без контура; у первых трёх язык не печатается.
_NO_LANGUAGE = frozenset({"no_grammar", "filtered", "deleted"})
_STATUS_LINES = {
    "no_grammar": "без грамматики",
    "filtered": "отфильтрован",
    "deleted": "удалён",
    "no_hunks": "нет hunk'ов (пустой файл или смена режима) - AST не выбирался",
    "diff_budget": "hunk'и не вошли в пакет по бюджету - AST не выбирался",
}


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
        "--window", type=int, help="окно модели в токенах (LLM_CONTEXT_WINDOW_TOKENS)"
    )
    parser.add_argument(
        "--context-max-tokens",
        type=int,
        help="потолок пакета контекста (CONTEXT_MAX_TOKENS)",
    )
    parser.add_argument(
        "--full-outline",
        action="store_true",
        help="печатать весь контур файла, а не только выбор",
    )
    parser.add_argument(
        "--payload",
        action="store_true",
        help="вместо отчёта - JSON: ast, omitted и omitted_count пакета каждого PR",
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
    """Проблемы PR. Пустой контур сюда не входит: это бывает и у нормального файла
    (`__init__.py` из docstring, модуль из констант) - его печатает `_print_file`."""
    problems = [f"дифф не получен: {pr.error}"] if pr.error else []
    problems += [
        f"{file.path}: {file.reason}"
        for file in pr.files
        if file.status == "skipped" and file.reason in _BROKEN_REASONS
    ]
    return problems


def _print_file(file: FileOutlineReport, *, full_outline: bool) -> None:
    if file.status in _NO_LANGUAGE:
        print(f"{file.path}  {_STATUS_LINES[file.status]}")
        return
    head = f"{file.path}  {file.language}"
    if file.status in _STATUS_LINES:
        print(f"{head}  {_STATUS_LINES[file.status]}")
        return
    print(f"{head}  изменены строки {compress_lines(file.touched)}")
    if file.status == "skipped":
        print(f"  AST пропущен: {file.reason or 'причина не вошла в перечень omitted'}")
        return
    print(f"  контур: определений {file.definitions}, импортов {file.imports}")
    if full_outline and file.outline:
        print("  весь контур:")
        print(_indented("\n".join(file.outline)))
    if file.scopes is not None:
        print("  в контекст (как видит модель):")
        print(_indented(file.scopes))
    if file.import_text is not None:
        print("  импорты:")
        print(_indented(file.import_text))
    if file.dropped:
        print(f"  отброшено бюджетом: {', '.join(file.dropped)}")
    if file.empty_outline:
        print(
            "  предупреждение: пустой контур - в непустом файле нет ни определений,"
            " ни импортов"
        )
    elif file.status == "no_scopes":
        print("  в контекст: ничего - правка вне определений, импортов нет")


def _omission(entry: dict[str, Any]) -> str:
    """Запись `omitted` коротко: `a.py:7 hunk`, `commit_message #2`, `pr_metadata`."""
    if "path" not in entry:
        index = f" #{entry['index']}" if "index" in entry else ""
        return f"{entry['part']}{index}"
    line = f":{entry['line']}" if "line" in entry else ""
    return f"{entry['path']}{line} {entry['part']}"


def _print_pr(pr: PrOutline, problems: list[str], *, full_outline: bool) -> None:
    print(f"=== [{pr.number}] {pr.id}: {pr.title}")
    for file in pr.files:
        _print_file(file, full_outline=full_outline)
    skipped = [e for e in pr.omitted if e["part"] == "ast"]
    if skipped:
        print(
            "пропущено AST: "
            + ", ".join(f"{e['path']} - {e['reason']}" for e in skipped)
        )
    dropped = [e for e in pr.omitted if e["reason"] == "budget"]
    if dropped:
        shown = ", ".join(_omission(e) for e in dropped[:DROPPED_LIMIT])
        rest = len(dropped) - DROPPED_LIMIT
        print(
            f"отброшено по бюджету: {shown}" + (f" … и ещё {rest}" if rest > 0 else "")
        )
    if pr.error is None:
        ast_files = len({item["path"] for item in pr.ast})
        print(
            f"пакет: файлов с AST: {ast_files}, ~{pr.ast_tokens} токенов;"
            f" весь пакет ~{pr.token_count} из {pr.max_tokens};"
            f" omitted: {len(pr.omitted)} из {pr.omitted_count}"
        )
    print("проблемы: " + ("; ".join(problems) if problems else "-"))


def _payload(result: OutlineResult) -> str:
    return json.dumps(
        [
            {
                "id": pr.id,
                "ast": pr.ast,
                "omitted": pr.omitted,
                "omitted_count": pr.omitted_count,
            }
            for pr in result.prs
        ],
        ensure_ascii=False,
        indent=2,
    )


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
        window_tokens=args.window,
        context_max_tokens=args.context_max_tokens,
    )
    started = time.monotonic()
    try:
        prs = _collect_inputs(args)
        result = run_outline(prs, config)
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

    problems = {pr.number: _problems(pr) for pr in result.prs}
    if args.payload:
        print(_payload(result))
    else:
        max_tokens = result.prs[0].max_tokens if result.prs else 0
        print(
            f"песочница tree-sitter: PR {len(result.prs)}, правила: {args.rules},"
            f" лимит пакета: {max_tokens} токенов"
        )
        for pr in result.prs:
            _print_pr(pr, problems[pr.number], full_outline=args.full_outline)
        with_ast = sum(len({item["path"] for item in pr.ast}) for pr in result.prs)
        total = sum(len(p) for p in problems.values())
        print(
            f"итого: PR {len(result.prs)}, файлов с AST {with_ast}, проблем {total},"
            f" время {elapsed:.1f} с"
        )
    if args.json is not None:
        try:
            args.json.write_text(
                json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as error:
            print(f"результат не записан в {args.json}: {error}", file=sys.stderr)
            return 2
        # При --payload stdout - чистый JSON, поэтому строка о файле - в stderr.
        print(
            f"результат: {args.json}", file=sys.stderr if args.payload else sys.stdout
        )
    return 1 if any(problems.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
