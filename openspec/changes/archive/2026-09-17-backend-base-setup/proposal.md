## Why

`backend-architecture` (PR #5) built the four layers, the persistence model
and the composition root, and deliberately deferred everything it labelled
DevOps or Base Setup territory: "No dependency-manager or linter migration
(uv, ruff, pylint) — that is Backend Base Setup #13/#20." `backend-ci-on-uv`
then made the checks that exist actually run in CI, but introduced none of
its own — `ruff` and `pytest` were already there.

What is still missing is the base-setup ticket itself: a static type-checking
gate, a commit-time check that catches a violation before it reaches CI, a
one-command local stack a new contributor can run without hand-provisioning
PostgreSQL, and a liveness endpoint distinct from the readiness-shaped
`/health` the architecture change shipped as a byproduct, not as a spec'd
contract.

This change delivers GitHub issue [#8](https://github.com/larchanka-training/dmc-268-api-t1/issues/8)
("Backend — Base Setup"), transferred into this repo from the shared
backlog repo `dmc-268-ui-t6`, where it is [#20](https://github.com/larchanka-training/dmc-268-ui-t6/issues/20).
`backend-architecture`'s proposal anticipated this landing as "#13/#20"
(quoted above); by the time this change opened its own issue, #13 had
already been taken by something else, so it was filed as #8 instead — the
next number GitHub actually had free.

Measured, not assumed: `ruff check .` already passes clean on this tree, uv
is already the only dependency manager, and `mypy` is not installed at all —
there is no `[tool.mypy]`, no dev dependency, nothing to gap-fill there but
the tool itself. Running `mypy --strict` against `app/` for this change's
research surfaced exactly five pre-existing type errors (a bare `dict`
missing type args in `app/domain/entities.py` and `app/infrastructure/db/models.py`,
a call to `Settings()` with no default in `app/config.py`, a covariant return
mismatch in `app/infrastructure/container.py`, and an `Any` leak in
`app/api/dependencies.py`) — small, named, and fixed as part of this change's
implementation, not a redesign.

## What Changes

- Add `mypy` as a uv dev dependency and a `[tool.mypy]` section running in
  strict mode over `app/`; add a `typecheck` step to CI's `lint` job,
  alongside the existing `ruff check .` and `lint-imports` steps.
- Fix the five strict-mode errors the current tree has today (see Why) so
  `mypy` starts green rather than merging with a baseline exemption list.
- Add a `pre-commit` config (managed as a uv dev dependency, run via
  `uv run pre-commit`) running `ruff check`, `mypy`, and `lint-imports` before
  a commit is created — the same three checks CI runs, just earlier.
- **Not** Husky: this repo has no `package.json` and no Node tooling anywhere
  in it; the frontend's Husky setup lives in the separate `dmc-268-ui-t6`
  repo. `pre-commit` is the Python-native equivalent and fits the existing
  `uv run <tool>` convention.
- **Not** Black: `ruff format` covers formatting and `ruff` is already the
  project's formatter/linter of record; adding Black would mean two tools
  disagreeing about the same job. Skipped per the ticket's own "only if
  actually required" wording.
- Add `docker-compose.yml` at the repo root wiring three services for local
  development: `api` (built from the existing root `Dockerfile`), `postgres`
  (`postgres:18-alpine`, matching the image and credentials CI already uses),
  and `redis` (`redis:7-alpine`) — as local infrastructure only, with no
  Python client dependency and no port/adapter, per the ticket's own
  instruction and consistent with `backend-architecture`'s "no Redis, and no
  premature ports for it either."
- The `api` service depends on `postgres`'s health check, runs
  `alembic upgrade head` before `uvicorn`, and reads `DATABASE_URL` from
  compose-provided environment rather than a developer's shell.
- No new route: `GET /health` already returns HTTP 200 with no database or
  Redis dependency, which is exactly what the task's "`GET /healthcheck` ->
  HTTP 200" line asks for. Decision made explicit in this change (per
  direct instruction): `/health` **is** the healthcheck; no second path is
  added.

Non-goals — these stay out of this change:

- No Redis client, port, adapter, or cache abstraction. Redis is a compose
  service and nothing else; `app/` gains zero new imports because of it. The
  first caller (idempotency, rate limiting, or cache) is a later ticket's
  job, exactly as `backend-architecture`'s design already commits to.
- No readiness endpoint that checks the database or Redis, and no separate
  `/healthcheck` path. `/health` already satisfies "`GET /healthcheck` ->
  HTTP 200" as written; a DB-aware readiness check is a different contract
  this ticket does not ask for.
- No change to the layering, the persistence model, the migrations, or any
  existing test. `lint-imports`' three contracts are unaffected.
- No Terraform or cloud-infrastructure change. `infra/` is DevOps ticket
  #12's territory; `docker-compose.yml` is local-only and does not touch it.
- No CI restructuring beyond adding one step. `backend-ci-on-uv` already
  fixed what runs; this change adds a `typecheck` step next to `lint`, not a
  new job topology.
- No pylint. `backend-ci-on-uv`'s design.md left that as an open question for
  Base Setup to decide; this change answers it "no" — `ruff` plus strict
  `mypy` is the static-analysis budget for now, and pylint would duplicate
  ruff's own rule set for no caught bug this change has found.

## Capabilities

### New Capabilities

- `backend-base-setup`: the developer-facing tooling contract — new tooling
  is added as a uv dependency and nowhere else, `mypy --strict` is a required
  gate over `app/`, the same checks run at commit time via `pre-commit` as in
  CI, a one-command `docker compose` stack runs the API against real
  PostgreSQL and Redis containers, Redis is provisioned without being wired
  to any application code, and `GET /health` is confirmed as the one
  liveness contract the task's "healthcheck" requirement refers to.

### Modified Capabilities

None. `backend-architecture`'s ports-and-adapters rule already forbids an
unused port (`Unused port is not committed`); Redis stays outside that
capability's scope because this change adds no port for it, so nothing in
that spec needs to change to stay true.

## Impact

**Code** — `app/domain/entities.py`, `app/infrastructure/db/models.py`,
`app/infrastructure/db/unit_of_work.py`, `app/api/dependencies.py` (the
strict-mode fixes; `app/config.py` needed none once the `pydantic.mypy`
plugin was enabled), five `tests/` files (real strict-mode findings the
`tests.*` override didn't cover — see design D1), `Dockerfile` (added
`alembic.ini`/`alembic/` so the image can migrate itself), a new
`.pre-commit-config.yaml`, a new `docker-compose.yml`. `app/api/factory.py`
and `tests/test_health.py` are unchanged — `/health` already satisfies the
healthcheck requirement.

**Dependencies** — adds `mypy` and `pre-commit` to `pyproject.toml`'s `dev`
extra and `uv.lock`. Adds no runtime dependency: no `redis` client package,
because nothing calls one yet.

**Infrastructure** — no change to `infra/` (Terraform/cloud). Introduces
`docker-compose.yml` as a purely local artifact; CI continues to run its own
`postgres:18-alpine` service container and does not invoke compose.

**Team coordination** — this is Backend Base Setup (api-t1 [#8](https://github.com/larchanka-training/dmc-268-api-t1/issues/8) /
ui-t6 [#20](https://github.com/larchanka-training/dmc-268-ui-t6/issues/20)), the ticket `backend-architecture` and
`backend-ci-on-uv` both named as the owner of the package-manager/linter
decision and explicitly deferred to. No other change
is in flight against the same files.
