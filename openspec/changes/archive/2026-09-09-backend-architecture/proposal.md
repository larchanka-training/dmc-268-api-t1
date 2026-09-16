## Why

The backend repo currently holds a two-endpoint FastAPI stub (`main.py`) with no layering, no persistence, and no defined boundaries toward the LLM or the VCS provider. Four other tickets (Base Setup, DevOps, System Design, Frontend Architecture) are about to land code against this repo in parallel, so the structural contract — where business logic lives, which direction dependencies point, and what the database looks like — has to be fixed before anyone writes review-pipeline code, or every team member invents their own layering.

This change delivers GitHub issue [#4](https://github.com/larchanka-training/dmc-268-api-t1/issues/4) ("Backend — Architecture"), transferred into this repo from the shared backlog repo `dmc-268-ui-t6`, where it was #15.

## What Changes

- Define the Clean/Hexagonal layering of the FastAPI service — `Router -> Service -> Repository -> Gateway` — as an enforced dependency rule, not a naming convention: the domain and application layers depend on abstract ports, and only the infrastructure layer imports SQLAlchemy or any vendor SDK. A CI check fails the build on an outward import.
- Pin the interpreter as a contract: `requires-python = "==3.14.*"` and a `.python-version` file. The models and domain entities do not import below 3.11, and today nothing stops an install on 3.9.
- Introduce the four layer packages and put the review pipeline's decision logic in the domain as **pure functions** — the `ReviewRun` transition table and the `DiffAnchor` validity rule — testable with no database, broker, or network.
- Design the PostgreSQL data model for the review pipeline and express it as SQLAlchemy v2 declarative models with typed `Mapped[...]` columns: `Repository`, `MergeRequest`, `ReviewRun` (the ticket's `ReviewJob`), `ContextPayload`, `Finding`, `PublishedComment`.
- Define the **persistence ports** — one repository port per aggregate plus a `UnitOfWork` transaction boundary — and their SQLAlchemy adapters, with a composition root that binds them. These have a caller today (the model tests) and are what the next pipeline ticket builds on.
- Wire Alembic and generate the baseline migration that creates the whole schema, with `alembic downgrade base` verified to reverse it.
- Write `BACKEND_ARCHITECTURE.md` documenting the layers, the dependency rule, the persistence ports and their adapters, the review lifecycle, and — as prose and diagram rather than code — the seams this change deliberately does not build yet.
- Ship the ERD as a Mermaid `erDiagram` source file plus a rendered image, kept next to the architecture document.
- Adopt **GitHub-first with a provider-agnostic seam**: the persisted model stays in provider-neutral terms (`MergeRequest` covers a GitHub PR) and carries an explicit provider discriminator, so adding GitLab later is an adapter plus an enum value — no schema migration. The `VcsGateway` port itself is specified in the document, not committed as code, because nothing calls it yet.
- Adopt **no Redis, and no premature ports for it either**: the architecture document specifies how idempotency, rate limiting, and caching will sit behind their own ports with PostgreSQL or in-process adapters first and a Redis adapter later. None of it is written until the endpoint that needs it exists.

Non-goals — these belong to other tickets or later changes, and this change deliberately stops short of them:

- No review business logic: no context builder, no prompt assembly, no LLM calls, no comment publication.
- **No ports without a caller.** `VcsGateway`, `LlmGateway`, `JobQueue`, `IdempotencyStore`, `RateLimiter`, and `CacheStore` are designed and documented in this change but not committed as code, and neither are the `accounts`, `idempotency_records`, or `rate_limit_counters` tables. A `Protocol` written a ticket before its first adapter is a guess that has to be rewritten; the document carries the intent at no cost. They land with the use cases that call them.
- No HTTP endpoints beyond what already exists, no authentication, and no worker process.
- No CI infrastructure, Terraform, or docker-compose work — that is DevOps ticket #12. This change only adds its own checks to the existing workflow.
- No dependency-manager or linter migration (uv, ruff, pylint) — that is Backend Base Setup #13/#20.

## Capabilities

### New Capabilities

- `backend-architecture`: The layering contract of the service — the four layers, the direction dependencies are allowed to point, the requirement that an external system is reached only through a port owned by an inner layer, and the rule that replacing an adapter requires no change above the infrastructure layer.
- `review-data-model`: The persistent domain model of the review pipeline — the entities, their relationships and identity, the `ReviewRun` lifecycle states and legal transitions, the uniqueness invariants, and how a `Finding` is anchored to coordinates in a diff.
- `database-migrations`: Schema evolution discipline — every schema change reaches the database through a reviewed Alembic revision, migrations are reversible, and the migration graph stays linear and consistent with the declarative models.

### Modified Capabilities

None — `openspec/specs/` is empty; this is the first change in the repo.

## Impact

**Code** — new packages under `app/` (`domain/`, `application/`, `infrastructure/`, `api/`), `alembic/` with `versions/0001_*.py`, `docs/BACKEND_ARCHITECTURE.md`, `docs/erd.mmd` + rendered image. Existing `main.py` becomes a thin entrypoint delegating to an app factory; its `/` and `/health` responses are unchanged.

**Dependencies** — adds `sqlalchemy>=2.0`, `alembic`, `psycopg[binary]`, `pydantic-settings` to `pyproject.toml`, `import-linter` to dev dependencies, and a `requires-python = "==3.14.*"` floor plus a `.python-version` file. Does not add `redis`, `ollama`, or `pika`: those land with the adapters that use them.

**Infrastructure** — introduces a runtime dependency on PostgreSQL and a `DATABASE_URL` setting. DevOps ticket #12 must provision it; until then local work runs against a developer-run Postgres. The default `pytest` run stays green without it — everything below the adapters is pure and the integration tests skip when `DATABASE_URL` is unset.

**Team coordination** — Backend Base Setup (#13/#20) owns tooling and will edit the same `pyproject.toml`; conflicts are limited to the dependency list. System Design (#9) owns the cross-service picture and is the upstream source for the queue and context-tier decisions this change consumes. This change assumes PRs [#1](https://github.com/larchanka-training/dmc-268-api-t1/pull/1) (AGENTS.md) and [#2](https://github.com/larchanka-training/dmc-268-api-t1/pull/2) (init/devops) merge first, and rebases on them rather than duplicating their files.
