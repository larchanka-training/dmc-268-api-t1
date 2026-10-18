"""Что документы утверждают о коде — сверено с кодом.

Проверяются только факты, которые на стороне кода есть в машиночитаемом виде.
Проза — нет, количество тестов — тоже: число, которое меняется от каждого
нового теста, сделало бы этот файл обузой, а не защитой. Эти факты уже
разъезжались с кодом, поэтому покрыты именно они.

Контракт HTTP API — `docs/openapi/openapi.json` — экспорт схемы из кода: тест следит,
что экспорт свежий и что схема описывает ошибки так, как их отдаёт сервер.
"""

import json
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.domain.lifecycle import _ALLOWED
from app.infrastructure.db import models  # noqa: F401
from app.infrastructure.db.base import Base

ROOT = Path(__file__).resolve().parent.parent
ERD = ROOT / "docs" / "erd.md"
ARCHITECTURE = ROOT / "docs" / "BACKEND_ARCHITECTURE.md"
PYPROJECT = ROOT / "pyproject.toml"
BACKEND_RULES = ROOT / ".agents" / "rules" / "backend.md"
README = ROOT / "README.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
PIPELINE_SPEC = ROOT / "docs" / "pipeline" / "PIPELINE_SPEC.md"
OPENAPI = ROOT / "docs" / "openapi" / "openapi.json"

# Ограничения, которые стоит зафиксировать: в обоих есть правило про NULL,
# которое легко потерять и невозможно заметить снаружи.
DOCUMENTED_CONSTRAINTS = {
    "findings": "uq_findings_anchor",
    "published_comments": "uq_published_comments_finding",
}


def compiled_unique_clauses(table_name: str) -> set[str]:
    """Все UNIQUE-выражения, которые PostgreSQL получит для этой таблицы."""
    ddl = str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=postgresql.dialect()))
    return {
        line.strip().rstrip(",")
        for line in ddl.splitlines()
        if "UNIQUE" in line
    }


def test_erd_prints_the_findings_constraint_as_declared() -> None:
    clause = next(c for c in compiled_unique_clauses("findings") if "uq_findings_anchor" in c)
    match = re.search(r"\(([^)]*)\)", clause)
    assert match is not None, f"no column list in {clause!r}"
    columns = match.group(1)
    text = ERD.read_text()
    assert "NULLS NOT DISTINCT" in text, f"{ERD} does not mention the NULL rule"
    assert f"({columns})" in text, (
        f"{ERD} does not print the findings key as declared.\n"
        f"  declared: ({columns})\n"
        f"  fix the constraint table row 'A finding cannot repeat in a run'"
    )


def test_erd_prints_the_published_comment_constraint_as_declared() -> None:
    clause = next(
        c for c in compiled_unique_clauses("published_comments")
        if "uq_published_comments_finding" in c
    )
    match = re.search(r"\(([^)]*)\)", clause)
    assert match is not None, f"no column list in {clause!r}"
    columns = match.group(1)
    text = ERD.read_text()
    assert f"NULLS NOT DISTINCT ({columns})" in text, (
        f"{ERD} does not print the published-comment key as declared.\n"
        f"  declared: NULLS NOT DISTINCT ({columns})"
    )


def declared_contract_count() -> int:
    return len(tomllib.loads(PYPROJECT.read_text())["tool"]["importlinter"]["contracts"])


NUMBER_WORDS = {"один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6}


def test_architecture_names_every_layering_contract() -> None:
    text = ARCHITECTURE.read_text()
    match = re.search(r"`import-linter` в CI проверяет (\w+) контракт", text)
    assert match, f"{ARCHITECTURE} no longer states how many contracts import-linter checks in CI (in Russian)"
    claimed = NUMBER_WORDS.get(match.group(1))
    assert claimed is not None, f"unrecognised count {match.group(1)!r} in {ARCHITECTURE}"
    assert claimed == declared_contract_count(), (
        f"{ARCHITECTURE} claims {claimed} contracts, pyproject.toml declares "
        f"{declared_contract_count()}. Update the sentence and the lint-imports "
        f"output block below it."
    )


def quoted_commands(path: Path) -> set[str]:
    """Каждая команда `uv ...` в блоке кода, без хвостового комментария."""
    return {
        line.split("#")[0].strip()
        for line in path.read_text().splitlines()
        if line.strip().startswith("uv ")
    }


def test_rules_only_name_commands_that_exist() -> None:
    """Правила, которые агенты читают всегда, не должны звать команду, которой нет."""
    known = CI.read_text() + README.read_text()
    unknown = sorted(c for c in quoted_commands(BACKEND_RULES) if c not in known)
    assert not unknown, (
        f"{BACKEND_RULES} names commands that appear in neither {CI} nor {README}:\n"
        + "\n".join(f"  {c}" for c in unknown)
        + "\nEither the command is wrong, or CI and the README have not caught up."
    )


def documented_transition_table() -> dict[str, set[str]]:
    """Таблица «Разрешённые переходы» из PIPELINE_SPEC.md как {состояние: {состояния}}."""
    text = PIPELINE_SPEC.read_text()
    section = text.split("### Разрешённые переходы", 1)[1]
    rows = re.findall(r"^\| `(\w+)` \| (.+) \|$", section, re.MULTILINE)
    assert rows, f"{PIPELINE_SPEC} has no rows under 'Разрешённые переходы'"
    return {current: set(re.findall(r"`(\w+)`", nexts)) for current, nexts in rows}


def test_pipeline_spec_transition_table_matches_lifecycle() -> None:
    """§2 заявляет, что таблица — ручное зеркало `_ALLOWED`, а не источник правды."""
    documented = documented_transition_table()
    actual = {state.value: {n.value for n in nexts} for state, nexts in _ALLOWED.items()}
    assert documented == actual, (
        f"{PIPELINE_SPEC} 'Разрешённые переходы' has drifted from "
        f"app/domain/lifecycle.py::_ALLOWED.\n"
        f"  doc:  {documented}\n"
        f"  code: {actual}"
    )


# --- Контракт API: экспорт схемы из кода -----------------------------------------

PROBLEM_MEDIA_TYPE = "application/problem+json"
TRACE_ID_HEADER_REF = "#/components/headers/X-Trace-ID"
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}


def load_openapi() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(OPENAPI.read_text(encoding="utf-8")))


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def operation_responses(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """(«МЕТОД /путь статус», статус, ответ) — для всех ответов всех операций."""
    return [
        (f"{method.upper()} {path} {status}", status, response)
        for path, item in document["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS
        for status, response in operation["responses"].items()
    ]


def is_error_status(status: str) -> bool:
    return status == "default" or status[0] in "45"


def test_exported_contract_is_up_to_date() -> None:
    """(а) `docs/openapi/openapi.json` — ровно то, что строит код; та же проверка, что `--check` в CI."""
    from scripts.export_openapi import STALE_MESSAGE, render

    assert OPENAPI.read_text(encoding="utf-8") == render(), f"{OPENAPI.relative_to(ROOT)}: {STALE_MESSAGE}"


def test_every_error_code_is_in_the_discriminated_union() -> None:
    """(б) Каждый `ErrorCode` — в `AnyProblem` с discriminator по `code`, у схемы кода свои константы."""
    from app.api.problems import PROBLEM_MODELS, TITLES, ErrorCode, problem_type

    assert set(PROBLEM_MODELS) == set(ErrorCode), "у каждого кода из ErrorCode должна быть модель в AnyProblem"
    schemas = load_openapi()["components"]["schemas"]
    any_problem = schemas["AnyProblem"]
    assert any_problem["discriminator"]["propertyName"] == "code"
    mapping = {code: ref_name(ref) for code, ref in any_problem["discriminator"]["mapping"].items()}
    assert set(mapping) == {code.value for code in ErrorCode}
    assert {ref_name(v["$ref"]) for v in any_problem["oneOf"]} == set(mapping.values())
    for code in ErrorCode:
        properties = schemas[mapping[code.value]]["properties"]
        assert properties["code"]["const"] == code.value
        assert properties["type"]["const"] == problem_type(code)
        assert properties["title"]["const"] == TITLES[code]
        assert set(schemas[mapping[code.value]]["required"]) >= {
            "type", "title", "status", "detail", "instance", "code", "trace_id"
        }


def test_every_error_response_is_problem_json() -> None:
    """(в) Каждый ответ 4xx/5xx и `default` — только `application/problem+json` с моделью ошибки;
    у каждого ответа — заголовок `X-Trace-ID`."""
    document = load_openapi()
    problem_schemas = {"AnyProblem"} | {
        ref_name(ref) for ref in document["components"]["schemas"]["AnyProblem"]["discriminator"]["mapping"].values()
    }
    problems = []
    for where, status, response in operation_responses(document):
        if response.get("headers", {}).get("X-Trace-ID") != {"$ref": TRACE_ID_HEADER_REF}:
            problems.append(f"{where}: нет заголовка X-Trace-ID")
        if not is_error_status(status):
            continue
        content = response.get("content", {})
        if set(content) != {PROBLEM_MEDIA_TYPE}:
            problems.append(f"{where}: тип содержимого {sorted(content)}, а нужен только {PROBLEM_MEDIA_TYPE}")
            continue
        ref = content[PROBLEM_MEDIA_TYPE]["schema"].get("$ref", "")
        if ref_name(ref) not in problem_schemas:
            problems.append(f"{where}: схема {ref!r} — не модель ошибки")
    assert not problems, "\n".join(problems)


def can_fail_validation(parameter: dict[str, Any]) -> bool:
    """Параметр, который может не пройти проверку: не просто строка (UUID, число, enum…)."""
    schema = parameter["schema"]
    return any(
        variant.get("type") not in ("string", "null") or set(variant) - {"type", "title", "description"}
        for variant in schema.get("anyOf", [schema])
    )


def test_no_framework_validation_error_in_the_contract() -> None:
    """(г) 422 описан `ValidationProblem`, а не `HTTPValidationError` FastAPI; у операции с телом
    или параметром, который может не пройти проверку, 422 объявлен."""
    document = load_openapi()
    schemas = document["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and "ValidationError" not in schemas
    assert "HTTPValidationError" not in OPENAPI.read_text(encoding="utf-8")
    missing = [
        f"{method.upper()} {path}"
        for path, item in document["paths"].items()
        for method, operation in item.items()
        if (operation.get("requestBody") or any(map(can_fail_validation, operation.get("parameters", []))))
        and "422" not in operation["responses"]
    ]
    assert not missing, f"у операций с параметрами нет ответа 422 VALIDATION_ERROR: {missing}"


def test_sse_status_event_matches_its_model() -> None:
    """Данные события `status` потока проходят `ReviewRunStatusEvent` и не несут лишних полей."""
    from app.api.review_events import ReviewRunStatusEvent
    from app.domain.enums import ReviewRunStatus
    from app.domain.run_events import StatusEvent, format_sse

    event = StatusEvent(
        review_run_id=UUID("00000000-0000-0000-0000-000000000001"),
        status=ReviewRunStatus.FAILED,
        last_progress_at=datetime(2026, 10, 11, 10, 0, tzinfo=UTC),
        failed_stage=ReviewRunStatus.ANALYSING,
        failure_reason="LLM_TIMEOUT",
    )
    data = json.loads(format_sse(event).split("data: ", 1)[1])
    assert set(data) == set(ReviewRunStatusEvent.model_fields)
    ReviewRunStatusEvent.model_validate(data)
