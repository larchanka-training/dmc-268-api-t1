"""Что документы утверждают о коде — сверено с кодом.

Проверяются только факты, которые на стороне кода есть в машиночитаемом виде.
Проза — нет, количество тестов — тоже: число, которое меняется от каждого
нового теста, сделало бы этот файл обузой, а не защитой. Эти два факта уже
разъезжались с кодом, поэтому покрыты именно они.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
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
OPENAPI = ROOT / "docs" / "openapi" / "openapi.yaml"
FINDING_SCHEMA = ROOT / "docs" / "openapi" / "finding.schema.json"
ERROR_SCHEMA = ROOT / "docs" / "openapi" / "error.schema.json"
REVIEW_JOB_SCHEMA = ROOT / "docs" / "openapi" / "review-job.schema.json"

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


def load_openapi_schemas() -> dict[str, Any]:
    document = yaml.safe_load(OPENAPI.read_text())
    return cast(dict[str, Any], document["components"]["schemas"])


# Аннотации не влияют на то, что схема принимает, и расходятся по делу: в json-схемах
# описания длиннее, у openapi есть example.
ANNOTATIONS = {"description", "example", "title", "$schema", "$id"}


def resolve_ref(ref: str) -> Any:
    """`#/components/schemas/X` — из openapi.yaml, `x.schema.json` — соседний файл."""
    if ref.startswith("#/components/schemas/"):
        return load_openapi_schemas()[ref.rsplit("/", 1)[-1]]
    return json.loads((OPENAPI.parent / ref).read_text())


def schema_shape(node: Any) -> Any:
    """Схема без аннотаций, с раскрытыми $ref и `required` без учёта порядка."""
    if isinstance(node, list):
        return [schema_shape(item) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return schema_shape(resolve_ref(node["$ref"]))
    shape: dict[str, Any] = {}
    for key, value in node.items():
        if key in ANNOTATIONS:
            continue
        if key == "properties":
            # Здесь ключи — имена полей, а не ключевые слова: не фильтруем.
            shape[key] = {name: schema_shape(prop) for name, prop in value.items()}
        elif key == "required":
            shape[key] = sorted(value)
        else:
            shape[key] = schema_shape(value)
    return shape


@pytest.mark.parametrize(
    ("schema_name", "path"),
    [
        ("Finding", FINDING_SCHEMA),
        ("ReviewJob", REVIEW_JOB_SCHEMA),
        ("ReviewError", ERROR_SCHEMA),
    ],
)
def test_json_schema_matches_openapi(schema_name: str, path: Path) -> None:
    """*.schema.json повторяют схему из openapi.yaml целиком: поля, required, enum'ы, ограничения."""
    doc_shape = schema_shape(json.loads(path.read_text()))
    api_shape = schema_shape(load_openapi_schemas()[schema_name])
    assert doc_shape == api_shape, (
        f"{path} has drifted from openapi.yaml components.schemas.{schema_name}.\n"
        f"  json:    {json.dumps(doc_shape, ensure_ascii=False, sort_keys=True)}\n"
        f"  openapi: {json.dumps(api_shape, ensure_ascii=False, sort_keys=True)}"
    )


def api_error_responses() -> list[tuple[str, str]]:
    """(где, description) для каждого ответа openapi.yaml, тело которого — ApiError."""
    document = yaml.safe_load(OPENAPI.read_text())
    found = []
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for status, response in operation["responses"].items():
                schema = response.get("content", {}).get("application/json", {}).get("schema", {})
                if schema.get("$ref") == "#/components/schemas/ApiError":
                    found.append((f"{method.upper()} {path} {status}", response["description"]))
    return found


def test_every_api_error_response_names_a_declared_code() -> None:
    """Код ошибки HTTP-слоя живёт в description ответа — сверяем его с enum ApiError.code."""
    declared = set(load_openapi_schemas()["ApiError"]["properties"]["code"]["enum"])
    used = set()
    for where, description in api_error_responses():
        codes = re.findall(r"ApiError\.code = (\w+)", description)
        assert len(codes) == 1, f"{OPENAPI} {where}: expected one 'ApiError.code = X', got {codes}"
        assert codes[0] in declared, f"{OPENAPI} {where}: {codes[0]} is not in ApiError.code enum"
        used.add(codes[0])
    assert used == declared, f"ApiError.code declares codes no response uses: {sorted(declared - used)}"
