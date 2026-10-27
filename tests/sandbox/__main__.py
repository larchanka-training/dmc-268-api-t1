"""CLI песочницы воркера: `uv run python -m tests.sandbox`.

Прогоняет PR через настоящий воркер на фейках и печатает, что воркер сделал:
статус прогона, шаги, замечания и опубликованное ревью. PR - кейсы корпуса eval
(`--case`), YAML-файлы в формате корпуса (`--case-file`) и диапазоны git
(`--from-git`); сырые сообщения очереди (`--message`) идут после них. Модель -
локальная Ollama (`--llm ollama`), заглушка (`stub`), оракул по разметке
(`oracle`) или сценарий из JSON (`script`). Код выхода: 0 - все PR `completed`
и подтверждены, 1 - есть nack или незавершённый прогон, 2 - вход или окружение
не готовы. Сырые сообщения в код выхода не входят: их дело - быть отвергнутыми.
"""

import argparse
import dataclasses
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import httpx

from app.application.ports.llm_gateway import LlmGateway
from app.infrastructure.llm.stub import StubLlmGateway
from app.worker.factory import WorkerConfigError

from ..eval.rules.corpus import CONFIGS, Case, load_corpus
from ..eval.rules.oracle import OracleLlmGateway
from ..fakes.github_api import FAULTS, FakeGitHub, FaultError
from ..fakes.llm_gateway import ScriptError, load_llm_script
from .inputs import (
    InputError,
    PrInput,
    from_case,
    from_git,
    load_case_file,
    load_raw_message,
)
from .worker import CaseResult, RawResult, SandboxConfig, run_sandbox

DEFAULT_OLLAMA_URL = "http://localhost:11434"
PREFLIGHT_TIMEOUT_SECONDS = 3.0
# Сколько символов тела комментария и сводки показывать в отчёте.
COMMENT_LIMIT = 200
SUMMARY_LIMIT = 600


def _parse_args(
    argv: Sequence[str] | None, case_ids: Sequence[str]
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tests.sandbox",
        description="Песочница воркера: настоящий воркер на фейках, живая только модель.",
    )
    parser.add_argument(
        "--llm",
        choices=("ollama", "stub", "oracle", "script"),
        default="ollama",
        help="модель: ollama - локальная Ollama, stub - пустые ответы, oracle - ровно"
        " размеченные нарушения кейса, script - сценарий из --llm-script",
    )
    parser.add_argument(
        "--llm-script",
        type=Path,
        metavar="PATH",
        help="JSON-сценарий ответов модели; обязателен для --llm script",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL", ""),
        help="модель Ollama (по умолчанию OLLAMA_MODEL); обязательна для --llm ollama",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL),
        help=f"адрес Ollama (по умолчанию OLLAMA_BASE_URL или {DEFAULT_OLLAMA_URL})",
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=case_ids,
        metavar="ID",
        help="id кейса корпуса; можно повторять. Без --case, --case-file, --from-git"
        " и --message - все кейсы: " + ", ".join(case_ids),
    )
    parser.add_argument(
        "--case-file",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="YAML-кейс в формате корпуса (раздел expected необязателен); можно повторять",
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
        "--message",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="сырое тело сообщения очереди, доставляется после PR; можно повторять",
    )
    parser.add_argument(
        "--redeliver",
        action="store_true",
        help="каждое сообщение PR приходит дважды, как при повторной доставке брокером",
    )
    parser.add_argument(
        "--github-fault",
        action="append",
        default=[],
        metavar="SPEC",
        help="сбой фейкового GitHub; можно повторять: "
        + "; ".join(f"{name} - {what}" for name, what in FAULTS.items()),
    )
    parser.add_argument(
        "--json",
        type=Path,
        metavar="PATH",
        help="записать результат прогона в JSON",
    )
    parser.add_argument(
        "--rules",
        choices=tuple(CONFIGS),
        default="rules",
        help="что лежит в .review/rules.md целевой ветки",
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
        "-v", "--verbose", action="store_true", help="логи воркера уровня INFO"
    )
    args = parser.parse_args(argv)
    if args.llm == "ollama" and not args.model:
        parser.error("для --llm ollama нужна --model или переменная OLLAMA_MODEL")
    return args


def _check_faults(specs: Sequence[str]) -> None:
    """Неизвестный сбой - `FaultError` до прогона, а не посреди него."""
    probe = FakeGitHub("", 0, "")
    for spec in specs:
        probe.apply_fault(spec, ())


def _collect_inputs(args: argparse.Namespace, by_id: dict[str, Case]) -> list[PrInput]:
    """PR в порядке --case, --case-file, --from-git; без входов - весь корпус."""
    prs = [from_case(by_id[i]) for i in args.case or ()]
    prs += [load_case_file(path) for path in args.case_file]
    prs += [from_git(spec, args.repo) for spec in args.from_git]
    if not prs and not args.message:
        prs = [from_case(case) for case in by_id.values()]
    return prs


def _llm_gateway(args: argparse.Namespace, prs: Sequence[PrInput]) -> LlmGateway | None:
    """Шлюз модели для `run_sandbox`; `None` - Ollama из настроек воркера."""
    if args.llm == "stub":
        return StubLlmGateway()
    if args.llm == "oracle":
        return OracleLlmGateway(
            [
                Case(
                    id=pr.id,
                    title=pr.title,
                    pr_title=pr.pr_title,
                    pr_description=pr.pr_description,
                    files={p: c for p, c in pr.head_files.items() if c is not None},
                    expected=pr.expected,
                )
                for pr in prs
            ]
        )
    if args.llm == "script":
        return load_llm_script(args.llm_script)
    return None


def _model_names(tags: object) -> set[str]:
    models = tags.get("models", []) if isinstance(tags, dict) else []
    names: set[str] = set()
    for model in models:
        for key in ("name", "model"):
            value = model.get(key) if isinstance(model, dict) else None
            if isinstance(value, str):
                names.add(value)
    return names


def _preflight_ollama(url: str, model: str) -> str | None:
    """Ollama отвечает и модель скачана; иначе - подсказка для stderr."""
    try:
        response = httpx.get(
            f"{url.rstrip('/')}/api/tags", timeout=PREFLIGHT_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        tags = response.json()
    except (httpx.HTTPError, ValueError) as error:
        return (
            f"Ollama недоступна по {url}: {error}\n"
            "Запустите ollama serve (или укажите --ollama-url), либо возьмите --llm stub."
        )
    names = _model_names(tags)
    wanted = {model, model.removesuffix(":latest"), f"{model}:latest"}
    if not names & wanted:
        available = ", ".join(sorted(names)) or "нет ни одной"
        return (
            f"Модели {model} нет в Ollama (скачаны: {available}).\n"
            f"Скачайте её: ollama pull {model}"
        )
    return None


def _cut(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} с"


def _print_case(result: CaseResult) -> None:
    print(f"\n=== [{result.number}] {result.case_id}: {result.title}")
    delivery = (
        "nack (ушло бы в DLQ)"
        if result.nacked
        else "ack"
        if result.acked
        else "не обработано"
    )
    print(
        f"статус: {result.status}; сообщение: {delivery}; модель: {result.model or '—'};"
        f" длительность: {_seconds(result.duration_seconds)}"
    )
    if result.failure_reason:
        print(f"причина сбоя: {result.failure_reason}")
    print("шаги:")
    for step in result.steps:
        extra = []
        if step.tokens_used is not None:
            extra.append(f"токенов {step.tokens_used}")
        if step.error_code:
            extra.append(f"ошибка {step.error_code}")
        tail = f" ({', '.join(extra)})" if extra else ""
        print(
            f"  - {step.name}: {step.status}, {_seconds(step.duration_seconds)}{tail}"
        )
    if not result.steps:
        print("  (нет)")
    print(f"замечаний в базе: {len(result.findings)}")
    comments = [c for review in result.reviews for c in review.comments]
    print(
        f"опубликовано ревью: {len(result.reviews)}, inline-комментариев: {len(comments)}"
    )
    for comment in comments:
        category = comment.category or "?"
        print(
            f"  {comment.path}:{comment.line} [{category}] {_cut(comment.body, COMMENT_LIMIT)}"
        )
    for review in result.reviews:
        if review.body:
            print(f"сводка ({review.event}): {_cut(review.body, SUMMARY_LIMIT)}")
    print("ожидалось:")
    for issue in result.expected:
        what = (
            f"правило {issue.rule[0]}#{issue.rule[1]}" if issue.rule else issue.category
        )
        print(f"  {issue.path}:{issue.line} [{what}] {issue.id}")
    if not result.expected:
        print("  (ничего - чистый PR)")
    if result.unexpected_requests:
        print("неожиданные запросы к GitHub:")
        for method, path in result.unexpected_requests:
            print(f"  {method} {path}")


def _print_raw(result: RawResult) -> None:
    delivery = (
        "nack (ушло бы в DLQ)"
        if result.nacked
        else "ack"
        if result.acked
        else "не обработано"
    )
    print(f"\n=== сырое сообщение {result.path_or_label}: {delivery}")
    if result.error:
        print(f"ошибка обработчика: {_cut(result.error, COMMENT_LIMIT)}")


def main(argv: Sequence[str] | None = None) -> int:
    corpus = load_corpus()
    by_id = {case.id: case for case in corpus}
    args = _parse_args(argv, tuple(by_id))
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.llm == "script" and args.llm_script is None:
        print("для --llm script нужен --llm-script PATH", file=sys.stderr)
        return 2
    try:
        _check_faults(args.github_fault)
        prs = _collect_inputs(args, by_id)
        messages = [load_raw_message(path) for path in args.message]
        llm = _llm_gateway(args, prs)
    except (InputError, ScriptError, FaultError) as error:
        print(error, file=sys.stderr)
        return 2

    if args.llm == "ollama":
        hint = _preflight_ollama(args.ollama_url, args.model)
        if hint is not None:
            print(hint, file=sys.stderr)
            return 2

    config = SandboxConfig(
        llm=args.llm,
        ollama_url=args.ollama_url,
        model=args.model,
        rules_config=args.rules,
        window_tokens=args.window,
        context_max_tokens=args.context_max_tokens,
    )
    faults = tuple(args.github_fault)

    def prepare_github(github: FakeGitHub, numbers: Sequence[int]) -> None:
        for spec in faults:
            github.apply_fault(spec, numbers)

    model = args.model if args.llm == "ollama" else args.llm
    print(
        f"песочница: {len(prs)} PR, сырых сообщений {len(messages)}, модель: {model},"
        f" правила: {args.rules}",
        flush=True,
    )
    started = time.monotonic()
    try:
        sandbox = run_sandbox(
            prs,
            config,
            llm=llm,
            raw_messages=messages,
            raw_labels=[str(path) for path in args.message],
            redeliver=args.redeliver,
            prepare_github=prepare_github if faults else None,
        )
    except WorkerConfigError as error:
        print(
            f"Воркер не стартует с такой конфигурацией окна: {error}\n"
            "Увеличьте --window или уменьшите --context-max-tokens.",
            file=sys.stderr,
        )
        return 2
    elapsed = time.monotonic() - started
    results = sandbox.cases

    for result in results:
        _print_case(result)
    for raw in sandbox.raw:
        _print_raw(raw)

    completed = sum(r.status == "completed" for r in results)
    nacked = sum(r.nacked for r in results)
    comments = sum(len(rv.comments) for r in results for rv in r.reviews)
    expected = sum(len(r.expected) for r in results)
    unexpected = sum(len(r.unexpected_requests) for r in results)
    print(
        f"\nитого: кейсов {len(results)}, completed {completed}, nack {nacked},"
        f" inline-комментариев {comments}, ожидалось нарушений {expected},"
        f" неожиданных запросов к GitHub {unexpected},"
        f" сырых сообщений {len(sandbox.raw)} (nack {sum(r.nacked for r in sandbox.raw)}),"
        f" время {elapsed:.1f} с"
    )
    if args.json is not None:
        args.json.write_text(
            json.dumps(dataclasses.asdict(sandbox), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"результат: {args.json}")
    return 0 if nacked == 0 and completed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
