"""Schema assertions that need no database.

These compile DDL against the PostgreSQL dialect and read it. The point is to
check what Postgres will actually be told, not what the Python annotation looks
like: a bare `Mapped[datetime]` reads correctly and produces a naive column.
"""

import re

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.domain.enums import TERMINAL_STATUSES
from app.infrastructure.db import models  # noqa: F401  (registers the tables)
from app.infrastructure.db.base import Base

DIALECT = postgresql.dialect()
TABLES = [
    "repositories",
    "merge_requests",
    "review_runs",
    "context_payloads",
    "findings",
    "published_comments",
]


def ddl(table_name: str) -> str:
    return str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=DIALECT))


def test_every_expected_table_is_mapped() -> None:
    assert sorted(Base.metadata.tables) == sorted(TABLES)


@pytest.mark.parametrize("table", TABLES)
def test_timestamps_carry_a_time_zone(table: str) -> None:
    statement = ddl(table)
    assert "TIMESTAMP WITHOUT TIME ZONE" not in statement
    assert statement.count("TIMESTAMP WITH TIME ZONE") >= 2


@pytest.mark.parametrize("table", TABLES)
def test_primary_key_is_a_native_uuid_without_a_database_default(table: str) -> None:
    line = next(line for line in ddl(table).splitlines() if line.strip().startswith("id "))
    assert "UUID" in line
    assert "DEFAULT" not in line


def test_one_active_run_per_commit_is_a_partial_unique_index() -> None:
    index = next(
        i
        for i in Base.metadata.tables["review_runs"].indexes
        if i.name == "uq_review_runs_one_active_per_commit"
    )
    statement = str(CreateIndex(index).compile(dialect=DIALECT))
    assert "UNIQUE INDEX" in statement
    assert "WHERE" in statement
    for terminal in TERMINAL_STATUSES:
        assert terminal.value in statement


def test_enum_columns_are_native_postgres_types() -> None:
    statement = ddl("findings")
    assert "finding_category" in statement
    assert "finding_severity" in statement


def test_context_payload_body_is_jsonb() -> None:
    assert "JSONB" in ddl("context_payloads")


def test_children_cascade_from_their_parent() -> None:
    for table in ("merge_requests", "review_runs", "context_payloads", "findings"):
        assert "ON DELETE CASCADE" in ddl(table)


def test_findings_cannot_repeat_an_anchor_in_one_run() -> None:
    """The whole anchor, and NULLS NOT DISTINCT so it fires on the old side too."""
    assert re.search(
        r"UNIQUE NULLS NOT DISTINCT "
        r"\(review_run_id, file_path, side, old_line, new_line, category\)",
        ddl("findings"),
    )


def test_a_finding_is_published_at_most_once_per_run() -> None:
    """NULLS NOT DISTINCT extends the limit to summaries, which carry no finding."""
    assert re.search(
        r"UNIQUE NULLS NOT DISTINCT \(review_run_id, finding_id\)",
        ddl("published_comments"),
    )


def test_same_full_name_under_two_providers_is_allowed() -> None:
    statement = ddl("repositories")
    assert re.search(r"UNIQUE \(provider, provider_id\)", statement)
    assert "UNIQUE (full_name)" not in statement
