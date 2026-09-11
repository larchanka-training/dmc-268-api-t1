"""The schema the migrations produce must be the schema the models describe."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.infrastructure.db import models  # noqa: F401
from app.infrastructure.db.base import Base

from ..conftest import requires_db


@pytest.mark.integration
@requires_db
def test_migrations_leave_no_drift(migrated) -> None:
    """Autogenerate against a migrated database must find nothing to do."""
    with migrated.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"schema and models disagree: {diff}"
