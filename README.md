# DMC-268 API (Team 1)

FastAPI backend for the automated code-review agent.

Architecture and the data model: [`docs/BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md),
[`docs/erd.md`](docs/erd.md).

## Requirements

- Python 3.14, pinned in `.python-version` and enforced by `requires-python`.
  `uv` fetches it for you; the system `python3` is very likely something else.
- [uv](https://docs.astral.sh/uv/) for dependencies.
- PostgreSQL, for running the service and the integration tests.

## Setup

```bash
uv sync --all-extras
```

## Run

The service reads `DATABASE_URL` and refuses to start without it. It does not
create tables: the schema arrives through migrations.

```bash
docker run -d --name dmc268-db -p 5432:5432 \
  -e POSTGRES_USER=dmc -e POSTGRES_PASSWORD=dmc -e POSTGRES_DB=dmc268 \
  postgres:18-alpine

export DATABASE_URL="postgresql+psycopg://dmc:dmc@localhost:5432/dmc268"

uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

`GET /health` answers `{"status": "ok"}`. The generated API docs are at `/docs`.

If you created a database from an earlier revision of this branch, drop and
recreate it. The baseline migration was corrected in place while it was still
unmerged, so an older database claims revision `0001` while carrying the
constraints it defined before the fix.

## Tests

Everything below the adapters is pure, so most of the suite needs nothing.

```bash
uv run pytest                           # 74 tests, no database required
TEST_DATABASE_URL=... uv run pytest     # 107 tests, adapters and migrations included
```

Tests that need PostgreSQL are marked `integration` and skip when
`TEST_DATABASE_URL` is unset.

That variable is deliberately not `DATABASE_URL`, and nothing falls back to it.
Preparing the test database drops its schema, so pointing the suite at the
database you run the service against would empty it. Give the tests their own:

```bash
createdb dmc268_test
export TEST_DATABASE_URL="postgresql+psycopg://dmc:dmc@localhost:5432/dmc268_test"
```

The role it connects as needs permission to create a role, because one test
provisions one to check that a percent-encoded password survives `alembic.ini`.

## Checks

```bash
uv run ruff check .      # style
uv run lint-imports      # the layering rule, see the architecture document
```

`lint-imports` fails the build when a module imports outward, for example when
anything under `app/domain` reaches for SQLAlchemy.

## Migrations

```bash
uv run alembic upgrade head        # apply
uv run alembic downgrade base      # reverse, leaves nothing behind
uv run alembic heads               # must report exactly one
```

Generated migrations are reviewed before they are committed. Conventions and
the `ALTER TYPE` recipe for extending an enum are in the architecture
document.
