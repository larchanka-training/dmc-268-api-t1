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


# --- Контракт openapi.yaml против роутов FastAPI ---------------------------------
#
# Сверяется то, что код отдаёт машиночитаемо через `app.openapi()`: какие ручки есть
# под /api/v1 и какие поля, `required` и nullable у тела успешного ответа. Типы и
# enum'ы не сверяются: модели ответа в коде их почти не сужают (status — просто str),
# и требование совпадения превратило бы тест в спор о том, где точнее описано.

API_PREFIX = "/api/v1"
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
# Пометка операции, которая описана в контракте как план, а роута у неё ещё нет.
NOT_IMPLEMENTED = "x-not-implemented"


def code_openapi() -> dict[str, Any]:
    """OpenAPI, который строит само приложение. Без БД и брокера: адреса только в настройках."""
    from app.api.factory import create_app
    from app.config import Settings

    settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="test-secret",
        public_origin="https://review.example",
    )
    return create_app(settings).openapi()


def operation_key(method: str, path: str) -> tuple[str, str]:
    """Ключ сравнения: имена параметров пути не важны (`{reviewId}` и `{review_run_id}` — одно)."""
    return method.upper(), re.sub(r"\{[^}]*\}", "{}", path)


def operations(document: dict[str, Any], prefix: str) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
    """{ключ: («МЕТОД /путь» без префикса, операция)} для путей документа."""
    found = {}
    for path, item in document["paths"].items():
        full = prefix + path
        if not full.startswith(API_PREFIX + "/"):
            continue
        short = full.removeprefix(API_PREFIX)
        for method, operation in item.items():
            if method in HTTP_METHODS:
                found[operation_key(method, short)] = (f"{method.upper()} {short}", operation)
    return found


def contract_operations() -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
    document = yaml.safe_load(OPENAPI.read_text())
    servers = [server["url"] for server in document.get("servers", [])]
    assert servers == [API_PREFIX], f"{OPENAPI}: ожидался единственный servers.url {API_PREFIX}, а там {servers}"
    return operations(document, API_PREFIX)


def test_openapi_lists_exactly_the_routes_of_the_app() -> None:
    """Каждая ручка /api/v1 из кода есть в контракте, и наоборот — кроме помеченных x-not-implemented."""
    contract = contract_operations()
    code = operations(code_openapi(), "")
    planned = {key for key, (_, operation) in contract.items() if operation.get(NOT_IMPLEMENTED) is True}

    only_code = sorted(code[key][0] for key in code.keys() - contract.keys())
    only_contract = sorted(contract[key][0] for key in contract.keys() - code.keys() - planned)
    implemented_planned = sorted(contract[key][0] for key in planned & code.keys())

    problems = []
    if only_code:
        problems.append(
            "Ручки есть в коде, но не описаны в контракте — добавьте их в openapi.yaml:\n"
            + "\n".join(f"  {name}" for name in only_code)
        )
    if only_contract:
        problems.append(
            "Ручки описаны в контракте, но роута в коде нет — уберите их из openapi.yaml "
            f"или пометьте `{NOT_IMPLEMENTED}: true`, если это план:\n"
            + "\n".join(f"  {name}" for name in only_contract)
        )
    if implemented_planned:
        problems.append(
            f"Ручки помечены `{NOT_IMPLEMENTED}: true`, но роут в коде уже есть — снимите "
            "пометку и сверьте описание с кодом:\n"
            + "\n".join(f"  {name}" for name in implemented_planned)
        )
    assert not problems, f"{OPENAPI} разошёлся с роутами FastAPI.\n" + "\n".join(problems)


def success_json_schema(operation: dict[str, Any]) -> tuple[str, Any] | None:
    """(код, схема) первого 2xx-ответа с телом application/json; None — такого ответа нет."""
    for status in sorted(operation.get("responses", {})):
        if not str(status).startswith("2"):
            continue
        content = operation["responses"][status].get("content", {})
        if "application/json" in content:
            return str(status), content["application/json"].get("schema", {})
    return None


def response_shape(node: Any, schemas: dict[str, Any]) -> dict[str, Any]:
    """Схема, сведённая к тому, что сверяем: поля, required, nullable — рекурсивно.

    nullable приходит в трёх записях: `nullable: true` (OpenAPI 3.0), `type: [X, "null"]`
    (так пишет openapi.yaml) и `anyOf: [{X}, {type: null}]` (так отдаёт pydantic v2).
    Все три сводятся к одному флагу, а схема без варианта null разбирается дальше.
    """
    if "$ref" in node:
        return response_shape(schemas[node["$ref"].rsplit("/", 1)[-1]], schemas)
    nullable = node.get("nullable") is True
    types = node.get("type")
    if isinstance(types, list) and "null" in types:
        nullable = True
        rest = [t for t in types if t != "null"]
        node = {**node, "type": rest[0] if len(rest) == 1 else rest}
    for combinator in ("anyOf", "oneOf"):
        if combinator not in node:
            continue
        variants = [v for v in node[combinator] if v.get("type") != "null"]
        if len(variants) < len(node[combinator]):
            nullable = True
        if len(variants) == 1:
            inner = response_shape(variants[0], schemas)
            return {**inner, "nullable": nullable or inner["nullable"]}
        return {
            "kind": "union",
            "nullable": nullable,
            "variants": [response_shape(v, schemas) for v in variants],
        }
    if "properties" in node:
        return {
            "kind": "object",
            "nullable": nullable,
            "required": sorted(node.get("required", [])),
            "fields": {name: response_shape(prop, schemas) for name, prop in node["properties"].items()},
        }
    if node.get("type") == "array" or "items" in node:
        return {"kind": "array", "nullable": nullable, "items": response_shape(node.get("items", {}), schemas)}
    return {"kind": "value", "nullable": nullable}


def shape_differences(code: dict[str, Any], contract: dict[str, Any], where: str) -> list[str]:
    """Расхождения двух сведённых схем — по строке на каждое, с путём до поля."""
    label = where or "тело ответа"
    if code["kind"] != contract["kind"]:
        return [f"{label}: в коде {code['kind']}, в контракте {contract['kind']}"]
    found = []
    if code["nullable"] != contract["nullable"]:
        found.append(
            f"{label}: nullable в коде — {code['nullable']}, в контракте — {contract['nullable']}"
        )
    if code["kind"] == "array":
        found += shape_differences(code["items"], contract["items"], f"{where}[]")
    elif code["kind"] == "union":
        if len(code["variants"]) != len(contract["variants"]):
            found.append(f"{label}: вариантов в коде {len(code['variants'])}, в контракте {len(contract['variants'])}")
        else:
            for i, (a, b) in enumerate(zip(code["variants"], contract["variants"], strict=True)):
                found += shape_differences(a, b, f"{where}<вариант {i}>")
    elif code["kind"] == "object":
        prefix = f"{where}." if where else ""
        code_fields, contract_fields = set(code["fields"]), set(contract["fields"])
        found += [f"поле {prefix}{name} есть в коде, но нет в контракте" for name in sorted(code_fields - contract_fields)]
        found += [f"поле {prefix}{name} есть в контракте, но нет в коде" for name in sorted(contract_fields - code_fields)]
        for name in sorted(code_fields & contract_fields):
            in_code, in_contract = name in code["required"], name in contract["required"]
            if in_code != in_contract:
                found.append(
                    f"поле {prefix}{name}: required в коде — {in_code}, в контракте — {in_contract}"
                )
            found += shape_differences(code["fields"][name], contract["fields"][name], f"{prefix}{name}")
    return found


def test_openapi_success_responses_match_the_app_models() -> None:
    """У каждой ручки из обоих источников тело успешного ответа совпадает по полям, required и nullable.

    Если у роута нет модели ответа (в коде схема пустая — редирект, произвольный dict),
    сверять нечего, и ручка пропускается. Если модель есть, а в контракте тела нет, — это
    расхождение.
    """
    contract_doc = yaml.safe_load(OPENAPI.read_text())
    code_doc = code_openapi()
    contract = contract_operations()
    code = operations(code_doc, "")

    problems = []
    for key in sorted(code.keys() & contract.keys()):
        name, code_operation = contract[key][0], code[key][1]
        code_response = success_json_schema(code_operation)
        contract_response = success_json_schema(contract[key][1])
        if code_response is None or not code_response[1]:
            continue
        if contract_response is None:
            problems.append(f"{name}: в коде у ответа {code_response[0]} есть модель, а в контракте у 2xx нет тела application/json")
            continue
        differences = shape_differences(
            response_shape(code_response[1], code_doc["components"]["schemas"]),
            response_shape(contract_response[1], contract_doc["components"]["schemas"]),
            "",
        )
        problems += [f"{name}: {difference}" for difference in differences]
    assert not problems, (
        f"{OPENAPI}: тела успешных ответов разошлись с моделями ответа в app/api.\n"
        + "\n".join(f"  {problem}" for problem in problems)
    )
