# DMC-268 API (Team 1)

FastAPI backend for the automated code-review agent.

Архитектура и модель данных: [`docs/BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md),
[`docs/erd.md`](docs/erd.md). Требования к поведению живут в `openspec/specs/` и меняются
только через OpenSpec-change.

## Задачи

Доска команды — GitHub Project [`dmc-268-t1`](https://github.com/orgs/larchanka-training/projects/7).
Бэкендовые тикеты заводятся в этом репозитории, фронтовые — в
[`dmc-268-ui-t1`](https://github.com/larchanka-training/dmc-268-ui-t1/issues). Доска собирает
и те, и другие.

## Правила разработки и агенты

Критичный минимум — в [`AGENTS.md`](AGENTS.md). Детали лежат в `.agents/`:

| Что | Где |
|---|---|
| Правила стека: команды, слои, швы, тесты | [`.agents/rules/backend.md`](.agents/rules/backend.md) |
| Ветки, задачи, пул-реквесты, работа с замечаниями | [`.agents/rules/git-and-pr.md`](.agents/rules/git-and-pr.md) |
| Скиллы: TDD, ревью, пул-реквест, миграции | [`.agents/skills/`](.agents/skills/) |
| Шаблоны кода и тестов | [`.agents/templates/backend/`](.agents/templates/backend/) |

Свой инструмент каждый подключает локально — каталоги инструментов не коммитятся:

```bash
ln -s ../.agents/skills .claude/skills    # или .cursor/, .codex/, .opencode/
openspec init --tools <tool>              # то же самое, если инструмент поддержан
ln -s AGENTS.md CLAUDE.md                 # Claude Code читает CLAUDE.md
```

Симлинк, а не копия: копия разойдётся с оригиналом на первой же правке. Antigravity исключение —
он читает `.agents/skills` сам.

Системные промпты ревью-агента, которого мы разрабатываем, —
[`prompts/review/README.md`](prompts/review/README.md).

## Requirements

- Python 3.14, pinned in `.python-version` and enforced by `requires-python`.
  `uv` fetches it for you; the system `python3` is very likely something else.
- [uv](https://docs.astral.sh/uv/) for dependencies.
- PostgreSQL, for running the service and the integration tests.

## Setup

```bash
uv sync --all-extras
uv run pre-commit install   # runs ruff, mypy and lint-imports before each commit
```

## Run

### Fastest path: Docker Compose

```bash
docker compose up
```

Starts the API together with PostgreSQL and Redis, waits for PostgreSQL to
report healthy, applies migrations, then serves the API at
`localhost:8000`. `GET /health` answers `{"status": "ok"}`. PostgreSQL's data
persists in a named volume across `docker compose down` / `up`; add `-v` to
`down` to discard it.

Redis is provisioned as local infrastructure only — nothing in the API talks
to it yet. It lands with the first feature that needs idempotency, rate
limiting, or caching (see `docs/BACKEND_ARCHITECTURE.md`, "Adding Redis").

### Dependency-free path: run PostgreSQL yourself

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

The whole cloud-shaped stack — PostgreSQL, RabbitMQ and the API container —
is also described as Terraform in [`infra/`](infra/README.md), which is the
DevOps ticket's deliverable and separate from the local `docker-compose.yml`
above.

If you created a database from an earlier revision of this branch, drop and
recreate it. The baseline migration was corrected in place while it was still
unmerged, so an older database claims revision `0001` while carrying the
constraints it defined before the fix.

## Tests

Everything below the adapters is pure, so most of the suite needs nothing.

```bash
uv run pytest                           # 77 tests, no database required
TEST_DATABASE_URL=... uv run pytest     # 111 tests, adapters and migrations included
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
uv run mypy .            # types, strict on app/
uv run lint-imports      # the layering rule, see the architecture document
```

`lint-imports` fails the build when a module imports outward, for example when
anything under `app/domain` reaches for SQLAlchemy. `mypy` runs in strict mode
over `app/`; `tests/` is held to a relaxed override (`[[tool.mypy.overrides]]`
in `pyproject.toml`) since pytest's own style — untyped fixtures, untyped
`def test_x():` — is the norm there. All three checks also run as
`pre-commit` hooks (`uv run pre-commit install`, see Setup) and in CI.

## Migrations

```bash
uv run alembic upgrade head        # apply
uv run alembic downgrade base      # reverse, leaves nothing behind
uv run alembic heads               # must report exactly one
```

Generated migrations are reviewed before they are committed. Conventions and
the `ALTER TYPE` recipe for extending an enum are in the architecture
document.
