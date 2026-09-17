## Context

See `proposal.md` — Why. What shapes the approach:

- `pyproject.toml` today has `[tool.ruff]` (target-version only, default rule
  set), `[tool.pytest.ini_options]`, and `[tool.importlinter]` with three
  contracts. No `[tool.mypy]`, no `mypy` dependency anywhere.
- `ruff check .` passes clean on the current tree (verified directly against
  `.venv/bin/ruff`, bypassing the `rtk` proxy hook, which fails to spawn
  `ruff` in this sandbox — a local tooling quirk, not a project issue).
- `mypy --strict app` (probed with `uv run --with mypy`, not committed)
  reports 5 errors in 5 files: two bare `dict` annotations, one
  `Settings()` call mismatched against a required field with no default,
  one covariant-return mismatch between `SqlAlchemyUnitOfWork` and the
  `UnitOfWork` port, one `Any` return leak in the DI accessor.
- `mypy --strict tests` reports 148 errors, nearly all `no-untyped-def` on
  pytest fixtures and test functions — the normal shape of a test suite that
  was never written against strict mode, not a sign of a deeper problem.
- No `.pre-commit-config.yaml`, no Husky, no `package.json`, no Node tooling
  anywhere in this repo. The frontend's Husky setup is a different repo.
- No `docker-compose.yml` anywhere. The root `Dockerfile` is a single-stage,
  `--no-dev`, production-shaped build; nothing in CI or the repo builds or
  runs it today, so this change is its first actual consumer. `infra/` is
  Terraform for cloud provisioning (DevOps ticket #12), separate from a
  local dev loop.
- `docs/BACKEND_ARCHITECTURE.md` already commits to "no Redis now,
  Redis-ready by construction": `IdempotencyStore`, `RateLimiter`, and
  `CacheStore` ports are documented but not built, and get a Redis adapter
  "the moment a second replica exists" — a future ticket, not this one.
- `GET /health` exists in `app/api/factory.py`, tested in
  `tests/test_health.py`, and is explicitly called out by the task as
  something to preserve, not replace.

## Goals / Non-Goals

**Goals:**

- A strict-mode type gate that starts green and stays green — no baseline
  exemption list to slowly pay down.
- The same three checks (`ruff`, `mypy`, `lint-imports`) catch a violation
  before it is committed, not just before it is merged.
- `docker compose up` is the whole local setup story for a new contributor:
  no manual `docker run` for Postgres, no separately-exported `DATABASE_URL`.
- Redis exists locally as a runnable container and nothing else. Zero new
  Python imports, zero ports, zero adapters.
- `GET /health` is confirmed as the liveness probe the task's "healthcheck"
  line refers to: always 200 once the process is up, regardless of database
  or cache state — true of it today, unchanged by this change.

**Non-Goals:**

- Redis client integration, idempotency, rate limiting, or caching. Those
  land with their first caller, per the architecture document's own
  commitment.
- A second `/healthcheck` route, and a readiness probe that reflects
  database/Redis health. `/health` already satisfies "`GET /healthcheck` ->
  HTTP 200" as written (D8); a readiness endpoint is a different, larger
  contract this change does not open.
- pylint. Left as an open question by `backend-ci-on-uv`; answered here as
  "not yet" (see D6).
- Any change to `infra/` (Terraform) or the CI job topology beyond one new
  step.

## Decisions

### D1 — `mypy --strict`, scoped to `app/`, `tests/` held to a relaxed override

Strict mode is enforced on `app/` (the ticket's own wording — "strict
mypy" — and the layer that ships to production). `tests/` gets a
`[[tool.mypy.overrides]]` block relaxing `disallow_untyped_defs`,
`disallow_incomplete_defs`, and `disallow_untyped_calls`, matching the
148-error probe above: those errors are pytest fixtures and helper factories
with no return-type contract to violate, not bugs. `mypy .` (the whole tree,
respecting the override) is what CI and `Definition of Done` run — not
`mypy app`, so the command in the ticket's own wording keeps working, but
`tests/` cannot fail it on missing annotations alone.

*Executed:* the third flag, `disallow_incomplete_defs`, was not in the
original probe's read of strict mode. It turned out to be load-bearing:
strict mode's `no-untyped-def` error also fires on a signature with *some*
but not all parameters annotated, which the first two flags alone did not
silence, and the tree has several of those in `tests/`.

*Alternative considered:* exclude `tests/` from mypy entirely. Rejected:
`tests/db/test_alembic_env.py:49` has a real `arg-type` error
(`make_url` called with `str | None`) that a full exclusion would hide, and
that class of error is exactly what strict mode is for.

*Executed — what the override did not paper over:* six real strict-mode
findings remained in `tests/` after the override, none of them
annotation-shaped: an implicit re-export of `Base` (`tests/test_docs.py`
importing it from `app.infrastructure.db.models`, which only re-exports it,
instead of its defining module `app.infrastructure.db.base`), two unchecked
`re.search(...).group()` calls in the same file, an unchecked `str | None`
read in `tests/conftest.py`, a generator fixture annotated to return
`Session` instead of `Iterator[Session]` in the same file, and an unchecked
`str | None` passed to `make_url` in `tests/db/test_alembic_env.py` — the
exact error this decision's "alternative considered" predicted. Each was
fixed with a narrow assertion, a corrected import, or a corrected
annotation; no test's behavior changed.

### D2 — Fix the five existing strict-mode errors as part of this change, not a followup

*Why:* `mypy .` passing is this ticket's own Definition of Done. Landing the
gate red and opening a followup ticket to fix it means the gate is not a
gate for as long as that followup is open — exactly the failure mode
`backend-ci-on-uv` documented for `TEST_DATABASE_URL` (a check that exists
but does not bite). Each of the five is a one-line annotation or a type
narrowing, not a design change:

- `app/domain/entities.py:91`, `app/infrastructure/db/models.py:165` — name
  the `dict`'s type arguments.
- `app/config.py:27` — `Settings()` is called with no arguments in
  `load_settings()`; mypy sees a required field with no default and no
  argument supplied. *Executed:* resolved by adding `plugins =
  ["pydantic.mypy"]` to `[tool.mypy]` — the plugin understands that a
  `BaseSettings` field with no Python-level default is legitimately
  env-sourced, and the error disappears with zero changes to
  `app/config.py` itself. No `type: ignore` needed.
- `app/infrastructure/container.py:24` — the mismatch is not in
  `container.py` itself but in what it calls: `SqlAlchemyUnitOfWork.__enter__`
  (`app/infrastructure/db/unit_of_work.py`) assigns its six repository
  attributes with no annotation, so mypy infers each one's type from the
  concrete `SqlAlchemy*` adapter on the right-hand side. `UnitOfWork` is a
  `Protocol` with these as plain (mutable, so invariant) attributes, and an
  inferred concrete-adapter type does not structurally match the port type
  the protocol declares. *Executed:* added an explicit port-typed annotation
  to each of the six assignments (`self.context_payloads: ContextPayloadRepo
  = SqlAlchemyContextPayloadRepo(session)`, and so on) — the fix belongs at
  the point where the attribute is actually assigned, not at
  `container.py`'s return statement, which was only where the mismatch
  surfaced.
- `app/api/dependencies.py:15` — the DI accessor pulls `app.state.container`
  (typed `Any` by FastAPI/Starlette) and returns it untyped; add an explicit
  `cast(Container, ...)` or an `isinstance` narrowing at that single seam,
  which is exactly where an `Any` boundary is supposed to be closed off.

*Risk:* the container/DI fix touches a file `backend-architecture` and
`backend-ci-on-uv` both consider settled. Kept minimal — a type annotation
change, not a restructuring — and covered by the existing
`tests/test_container.py`.

### D3 — `pre-commit`, not Husky

*Why:* Husky is a Node.js tool gated behind a `package.json`; this repo has
none, and introducing one purely to host a git-hook manager would be a new
dependency ecosystem for a repo that is otherwise 100% `uv`. `pre-commit` is
Python-native, installs as a uv dev dependency, and runs the same three
binaries (`ruff`, `mypy`, `lint-imports`) CI runs — no rule ever exists in
only one place.

*Alternative considered:* a plain `.git/hooks/pre-commit` shell script.
Rejected: not version-controlled by default (`.git/` is not committed), so
every clone loses it silently; `pre-commit`'s config file is committed and
`pre-commit install` is one command in the README's setup section.

### D4 — Black stays out

*Why:* `ruff format` already does this job, and `[tool.ruff]` is already the
project's one formatting authority. Running Black alongside Ruff's formatter
means the two can disagree about the same line and fight in CI; the ticket's
own wording ("Black only if actually required") is satisfied by not needing
it. If a future need for Black-specific behavior Ruff's formatter does not
cover appears, that is a new decision made against a concrete gap, not
speculatively here.

### D5 — Redis as a compose service with no client, no port

*Why:* the ticket says "Redis should initially be treated as local
infrastructure only... Do not introduce Redis ports/adapters/cache
abstractions without an existing caller," which is the same rule
`backend-architecture`'s design already committed to independently
("no Redis now, Redis-ready by construction"). The two instructions agree,
so this change does the minimum that satisfies both: a `redis:7-alpine`
service in `docker-compose.yml`, reachable at `redis:6379` from the `api`
container over the compose network, and zero lines of Python referencing it.

*What this deliberately does not do:* add `redis` (the Python package) to
`pyproject.toml`. There is no caller, so there is nothing for the dependency
to serve — adding it now would be the exact premature-dependency pattern the
architecture document argues against.

### D6 — pylint stays out, again

*Why:* `backend-ci-on-uv`'s design.md left this as an explicit open question
for Base Setup. `ruff` already runs the default rule set and passes clean;
strict `mypy` catches the type-safety class of bug pylint would otherwise
flag. Adding a third linter without a rule it uniquely catches is cost with
no measured benefit — consistent with this repo's own stated rule to
"justify any abstraction [or tool] that has no second caller today."

### D7 — `docker-compose.yml` builds the existing Dockerfile with one addition; no dev stage

The `api` service in compose uses `build: .` against the existing root
`Dockerfile`. No second Dockerfile stage is added for this change.

*Why:* the Dockerfile task in Definition of Done is already satisfied by the
file that exists; compose's job is orchestration, not rebuilding what is
already there. `uvicorn --reload`-style hot reload would need a bind-mounted
source tree and dev dependencies in the image, which is a real but separate
improvement — noted as an Open Question below rather than built speculatively.

*Executed — one gap the "as-is" framing missed:* the Dockerfile copied
`app/` but never `alembic.ini` or `alembic/`. It could build and run the API
image, but nothing inside it could run a migration — `alembic upgrade head`
would fail on a missing config file. Added two `COPY` lines for
`alembic.ini` and `alembic/`, immediately after the existing `COPY app
./app`. `.dockerignore` already excludes `.venv`/`__pycache__`, so the added
copy pulls in only the migration scripts and config, verified by the image
actually applying `0001, baseline schema` on `docker compose up`.

*Migration ordering:* the `api` service's command runs
`alembic upgrade head` before `uvicorn`, and `depends_on: postgres:
condition: service_healthy` ensures Postgres accepts connections first. This
mirrors what the README already tells a developer to do by hand
(`uv run alembic upgrade head` then `uv run uvicorn ...`), just inside the
container.

*Executed — a second, unrelated gap found while proving this works:*
`postgres:18-alpine`'s entrypoint refuses to start against a volume mounted
at `/var/lib/postgresql/data` — the pre-18 convention — logging "PostgreSQL
data in /var/lib/postgresql/data (unused mount/volume)" and exiting. Fixed
by mounting the named volume at `/var/lib/postgresql` instead, which is
where 18+'s image expects it (it places a major-version subdirectory
underneath). Verified: the stack starts, and a row written before `docker
compose down` (no `-v`) is still present after `docker compose up` runs
again, with no `initdb` in the restart's logs.

### D8 — `/health` **is** the healthcheck; no second route

*Superseded by direct instruction.* The original draft of this decision read
the task's "Preserve `/health`. The task additionally requires
`/healthcheck`" as two distinct routes. Overridden: `/health` already
returns HTTP 200 with no database or Redis dependency, which is the entire
content of "`GET /healthcheck` -> HTTP 200." Adding a second, identical
route would be two paths for one contract with nothing distinguishing them
— the kind of unused surface this repo's own rules argue against ("justify
any abstraction that has no second caller today"). No route, no test, and
no line of `app/` changes for this requirement; it is satisfied by the
code that already exists.

## Risks / Trade-offs

- **Fixing five files makes this change touch code `backend-architecture`
  considers closed.** → Each fix is a type annotation or a `cast`, verified
  against the existing test suite (`test_container.py`, `test_health.py`,
  `test_docs.py`); no behavior changes, only what mypy can prove about it.
- **`pre-commit` is one more thing to `uv sync` and `pre-commit install`
  before a hook fires.** → One-time, documented in the README's Setup
  section; a missing install means hooks silently don't run, which CI's own
  `typecheck`/`lint`/`test` jobs catch regardless.
- **`docker-compose.yml` and CI's own Postgres service container are two
  descriptions of "a Postgres for this project" that could drift.** →
  Accepted for now: compose is for local dev, CI's service container is for
  CI, and they already use the same image tag and credentials pattern by
  convention (`postgres:18-alpine`, database `dmc268`). If the credentials or
  version drift apart in review, that is a one-line fix, not a redesign.
- **Redis with no consumer is a container someone has to explain the
  presence of.** → The ticket asks for it explicitly ("Redis... PostgreSQL
  and Redis" in Requirements), and `docker-compose.yml`'s comment next to the
  service says why it has no client yet.

## Migration Plan

1. Add `mypy` and `pre-commit` to `pyproject.toml`'s `dev` extra; run
   `uv sync --all-extras` to update `uv.lock`.
2. Add `[tool.mypy]` (strict, `app/` in scope) and the `tests/` override;
   fix the five errors named in D2; verify `mypy .` reports zero errors.
3. Add `.pre-commit-config.yaml` running `ruff check`, `mypy`, `lint-imports`
   as local hooks (`language: system`, invoked through the project's own
   venv so the hook and CI resolve the same tool versions); document
   `uv run pre-commit install` in the README.
4. Add the `typecheck` step to `.github/workflows/ci.yml`'s `lint` job,
   alongside the existing `ruff check .` and `lint-imports` steps.
5. Add `docker-compose.yml` (`api`, `postgres`, `redis`); verify
   `docker compose up` serves `GET /health` at `localhost:8000` against the
   compose-provisioned Postgres, with Redis running and unused.
6. Confirm `GET /health` satisfies the healthcheck requirement as-is (D8);
   no route or test change needed.
7. Update the README's Setup/Run sections to add the `docker compose up` path
   as the fast-start option, keeping the existing manual `uv run` path as the
   dependency-free alternative it already documents itself as.

**Rollback:** each piece is independently revertable — the compose file, the
pre-commit config, and the mypy section touch no runtime behavior other than
the five annotation fixes, all covered by the existing test suite.

## Open Questions

- Whether the `tests/` mypy override (D1) should be narrower or wider than
  "relax untyped defs/calls" once written against the real remaining error
  list rather than this probe — a call for the implementation step, not a
  blocker for this proposal.
- Whether `docker-compose.yml`'s `api` service should get a `--reload`-friendly
  dev build (bind mount plus dev dependencies) in this change or a later one
  (D7). Proposed default: later — the Definition of Done asks for `docker
  compose up` to start the stack, not for hot reload inside it.
