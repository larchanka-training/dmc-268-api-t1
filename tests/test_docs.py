"""What the documents claim about the code, checked against the code.

Only facts that exist in machine-readable form on the code side are checked
here. Prose is not, and neither are test counts: a number that changes when
anyone adds a test would make this file a nuisance rather than a guard. These
two drifted once already, which is why they are the ones covered.
"""

import re
import tomllib
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.infrastructure.db.models import Base

ROOT = Path(__file__).resolve().parent.parent
ERD = ROOT / "docs" / "erd.md"
ARCHITECTURE = ROOT / "docs" / "BACKEND_ARCHITECTURE.md"
PYPROJECT = ROOT / "pyproject.toml"

# The constraints worth pinning: both carry a NULL rule that is easy to drop
# and impossible to notice from the outside.
DOCUMENTED_CONSTRAINTS = {
    "findings": "uq_findings_anchor",
    "published_comments": "uq_published_comments_finding",
}


def compiled_unique_clauses(table_name: str) -> set[str]:
    """Every UNIQUE clause PostgreSQL would receive for this table."""
    ddl = str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=postgresql.dialect()))
    return {
        line.strip().rstrip(",")
        for line in ddl.splitlines()
        if "UNIQUE" in line
    }


def test_erd_prints_the_findings_constraint_as_declared() -> None:
    clause = next(c for c in compiled_unique_clauses("findings") if "uq_findings_anchor" in c)
    columns = re.search(r"\(([^)]*)\)", clause).group(1)
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
    columns = re.search(r"\(([^)]*)\)", clause).group(1)
    text = ERD.read_text()
    assert f"NULLS NOT DISTINCT ({columns})" in text, (
        f"{ERD} does not print the published-comment key as declared.\n"
        f"  declared: NULLS NOT DISTINCT ({columns})"
    )


def declared_contract_count() -> int:
    return len(tomllib.loads(PYPROJECT.read_text())["tool"]["importlinter"]["contracts"])


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def test_architecture_names_every_layering_contract() -> None:
    text = ARCHITECTURE.read_text()
    match = re.search(r"`import-linter` runs in CI with (\w+) contracts", text)
    assert match, f"{ARCHITECTURE} no longer states how many contracts run in CI"
    claimed = NUMBER_WORDS.get(match.group(1))
    assert claimed is not None, f"unrecognised count {match.group(1)!r} in {ARCHITECTURE}"
    assert claimed == declared_contract_count(), (
        f"{ARCHITECTURE} claims {claimed} contracts, pyproject.toml declares "
        f"{declared_contract_count()}. Update the sentence and the lint-imports "
        f"output block below it."
    )
