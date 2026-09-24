"""Что документы утверждают о коде — сверено с кодом.

Проверяются только факты, которые на стороне кода есть в машиночитаемом виде.
Проза — нет, количество тестов — тоже: число, которое меняется от каждого
нового теста, сделало бы этот файл обузой, а не защитой. Эти два факта уже
разъезжались с кодом, поэтому покрыты именно они.
"""

import re
import tomllib
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.infrastructure.db import models  # noqa: F401
from app.infrastructure.db.base import Base

ROOT = Path(__file__).resolve().parent.parent
ERD = ROOT / "docs" / "erd.md"
ARCHITECTURE = ROOT / "docs" / "BACKEND_ARCHITECTURE.md"
PYPROJECT = ROOT / "pyproject.toml"
BACKEND_RULES = ROOT / ".agents" / "rules" / "backend.md"
README = ROOT / "README.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"

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
