"""Shared fixtures.

Integration tests need a live PostgreSQL. They are marked and skipped when
TEST_DATABASE_URL is unset, so a plain `pytest` stays green on a machine
without one. That is deliberate: everything worth testing below the adapters
is pure.

The variable is deliberately not DATABASE_URL, and there is no fallback to it.
`migrated` drops the schema, and DATABASE_URL is what the README tells you to
export for uvicorn and alembic. Sharing the two would mean a normal working
shell is one `pytest` away from an empty development database.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="needs a live PostgreSQL; set TEST_DATABASE_URL",
)


@pytest.fixture(scope="session")
def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("needs a live PostgreSQL; set TEST_DATABASE_URL")
    return create_engine(TEST_DATABASE_URL, future=True)


@pytest.fixture(scope="session")
def migrated(engine):
    """A database at head, rebuilt once per session."""
    from alembic.config import Config

    from alembic import command

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    config = Config("alembic.ini")
    # Point alembic at the test database explicitly. env.py honours an option
    # the caller already set, so the migration cannot wander off to whatever
    # DATABASE_URL happens to hold.
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.upgrade(config, "head")
    return engine


@pytest.fixture
def clean_db(migrated):
    """Start each test from an empty database.

    The session fixture isolates a test from its own writes by rolling back,
    but not from writes another test committed. Truncating first is what keeps
    the suite order-independent.
    """
    with migrated.begin() as conn:
        conn.execute(text("TRUNCATE repositories, merge_requests, review_runs, "
                          "context_payloads, findings, published_comments CASCADE"))
    return migrated


@pytest.fixture
def session(clean_db) -> Session:
    """A session rolled back after each test, so tests do not see each other."""
    connection = clean_db.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()
