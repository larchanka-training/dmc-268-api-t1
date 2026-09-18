## 1. Dependency management stays on uv

- [x] 1.1 Add `mypy` and `pre-commit` to `pyproject.toml`'s `dev` optional
  dependencies; run `uv sync --all-extras` and commit the updated
  `uv.lock`; verify a fresh clone plus `uv sync --all-extras` installs both
  with no second dependency list touched (spec: backend-base-setup — New
  tooling is managed through uv) — done; `uv sync --all-extras` installed
  `mypy==2.3.1` and `pre-commit==4.6.2` from `uv.lock`
- [x] 1.2 Verify `grep -rn "pip install\|requirements.txt" README.md
  .github/ Dockerfile docker-compose.yml` returns nothing naming `mypy` or
  `pre-commit` outside the uv-managed dependency list — confirmed, no matches

## 2. Strict mypy, starting green

- [x] 2.1 Add `[tool.mypy]` to `pyproject.toml`: strict mode, `python_version`
  matching `requires-python`, scoped to check `app/` and `tests/`; add a
  `[[tool.mypy.overrides]]` for `tests.*` relaxing `disallow_untyped_defs`
  and `disallow_untyped_calls` per design D1 — also added
  `disallow_incomplete_defs = false` to the override (strict mode's
  `no-untyped-def` also fires on partially-annotated signatures, which the
  original two flags alone did not cover) and `plugins = ["pydantic.mypy"]`
  (needed for 2.3)
- [x] 2.2 Fix `app/domain/entities.py:91` and
  `app/infrastructure/db/models.py:165`: give the bare `dict` its type
  arguments; verify `mypy` no longer reports `type-arg` on either file —
  done, both now `dict[str, Any]`
- [x] 2.3 Fix `app/config.py:27`'s `Settings()` call in `load_settings()`
  (design D2) — resolved via the `pydantic.mypy` plugin rather than a
  `type: ignore`; probed directly (`mypy --config-file ... app/config.py`
  with the plugin enabled) → `Success: no issues found`. No code change to
  `app/config.py` itself was needed
- [x] 2.4 Fix `app/infrastructure/container.py:24`'s covariant-return
  mismatch by typing the container's fields as the port types the
  composition root already treats them as, not the concrete `SqlAlchemy*`
  adapter types — done in `app/infrastructure/db/unit_of_work.py::__enter__`
  (that is where the untyped assignments actually live); verified `mypy`
  clean and `tests/test_container.py` still passes
- [x] 2.5 Fix `app/api/dependencies.py:15`'s `Any` leak with an explicit
  narrowing (`cast`) at the `app.state.container` boundary — done
- [x] 2.6 Verify `uv run mypy .` reports zero errors on the whole tree
  (spec: backend-base-setup — Static type checking gate) — confirmed:
  `Success: no issues found in 48 source files`. Getting there also required
  fixing six pre-existing strict-mode findings in `tests/` that the original
  probe's `tests.*` override did not cover (an implicit re-export of `Base`
  in `tests/test_docs.py`, two unchecked `re.search(...).group()` calls in
  the same file, an unchecked `str | None` in `tests/conftest.py`, a
  generator fixture typed to return `Session` instead of `Iterator[Session]`
  in the same file, and an unchecked `str | None` passed to `make_url` in
  `tests/db/test_alembic_env.py`) — each fixed with a narrow assertion, a
  corrected import, or a corrected return-type annotation; no test's
  behavior changed
- [x] 2.7 Prove the gate bites: temporarily removed the return type from
  `load_settings` in `app/config.py`, ran `mypy .`, confirmed it failed
  naming `app/config.py` and the function, then reverted (spec:
  backend-base-setup — Static type checking gate)

## 3. Pre-commit, mirroring CI

- [x] 3.1 Add `.pre-commit-config.yaml` with `language: system` hooks
  invoking `ruff check`, `mypy`, and `lint-imports` through the project's own
  `uv run`, so the hook and CI resolve the same tool versions from the same
  lockfile
- [x] 3.2 Document `uv run pre-commit install` in the README's Setup section
- [x] 3.3 Verify `uv run pre-commit run --all-files` passes on a clean tree —
  confirmed, all three hooks report `Passed`
- [x] 3.4 Prove it bites: staged `app/config.py` with an appended unused
  `import os`, ran `pre-commit run --files app/config.py`, confirmed the
  `ruff check` hook failed with `F401` and exit code 1 while the other two
  hooks still ran and passed, then reverted (spec: backend-base-setup —
  Pre-commit hook enforces the same checks before a commit is made)

## 4. CI gains the typecheck step

- [x] 4.1 Add a `uv run mypy .` step to the `lint` job in
  `.github/workflows/ci.yml`, alongside the existing `ruff check .` and
  `lint-imports` steps, each still reporting its own pass/fail independently
- [ ] 4.2 Verify a pull request with a strict-mode violation fails only the
  new step, leaving `ruff` and `lint-imports` green on the same diff — not
  run: this needs an actual GitHub Actions run against a pull request, which
  this change did not open. The equivalent was verified locally in 2.7 (mypy
  fails in isolation) and 3.4/pre-commit (ruff fails in isolation); the
  workflow step itself is a direct copy of the local command

## 5. Docker Compose local environment

- [x] 5.1 Add `docker-compose.yml` at the repo root: `postgres`
  (`postgres:18-alpine`, credentials matching CI's `dmc268`/`dmc268`/`dmc268`,
  a named volume, a health check), `redis` (`redis:7-alpine`, health check
  only), `api` (`build: .`, `depends_on: postgres: condition:
  service_healthy`, `DATABASE_URL` pointed at the `postgres` service,
  command running `alembic upgrade head` then `uvicorn`) — also had to add
  `alembic.ini` and `alembic/` to the Dockerfile's `COPY` list; the existing
  image built and ran the API but had no migration files in it, so `alembic
  upgrade head` could not have worked inside the container before this
- [x] 5.2 Verify `docker compose up` on a clean checkout brings up all three
  containers, `api` reaches a healthy state after `postgres` is healthy
  (spec: backend-base-setup — Containerized local development environment)
  — confirmed; also had to change the Postgres volume mount from
  `/var/lib/postgresql/data` to `/var/lib/postgresql`: `postgres:18-alpine`'s
  entrypoint refuses to start against the pre-18 mount point on an empty
  volume ("PostgreSQL data in /var/lib/postgresql/data (unused mount/volume)")
- [x] 5.3 Verify `curl localhost:8000/health` returns HTTP 200 against the
  compose stack — confirmed, `{"status":"ok"}`
- [x] 5.4 Verify `docker compose down` followed by `docker compose up`
  preserves previously written data through the named Postgres volume —
  confirmed: inserted a row into `repositories`, ran `down` (no `-v`) then
  `up`, the row was still present and Postgres logged no `initdb` on restart
- [x] 5.5 Verify `grep -rn "redis" app/ pyproject.toml` matches nothing:
  no Redis client import, no Redis dependency (spec: backend-base-setup —
  Redis is provisioned but not wired to application code) — confirmed; the
  only match anywhere is a pre-existing prose comment in
  `app/domain/staleness.py` explaining why a TTL-based approach was *not*
  taken, not a reference to a Redis dependency
- [x] 5.6 Update the README with a `docker compose up` quick-start, keeping
  the existing manual `uv run` path documented as the alternative it already
  is

## 6. Healthcheck requirement is satisfied by `/health` (no new route)

- [x] 6.1 Confirm `GET /health` returns HTTP 200 (spec: backend-base-setup —
  Liveness check endpoint); no route added, no test added — confirmed via
  the compose stack (5.3) and the existing `test_health` in
  `tests/test_health.py`, both green, neither touched
- [x] 6.2 Verify `/health` returns 200 with no `DATABASE_URL` reachable —
  confirmed: stopped the `postgres` container while `api` kept running,
  `curl localhost:8000/health` still returned 200
- [x] 6.3 Verify no `/healthcheck` route exists — confirmed,
  `curl localhost:8000/healthcheck` returns 404

## 7. Close out

- [x] 7.1 Run the full local verification loop matching Definition of Done:
  `docker compose up` starts the stack (5.2), `uv run ruff check .` passes,
  `uv run mypy .` passes, `curl -f localhost:8000/health` returns 200 — all
  confirmed
- [ ] 7.2 Verify the whole CI workflow green on a pull request — not run, no
  PR opened from this change yet. Locally equivalent: `ruff check .`,
  `lint-imports`, `mypy .` all pass, and the full suite is `111 passed, 0
  skipped` against a live PostgreSQL (`TEST_DATABASE_URL` set), matching
  what the `test` job's zero-skipped assertion requires
- [ ] 7.3 Reference GitHub issue #8 ("Backend — Base Setup", transferred
  from `dmc-268-ui-t6` #20) in the pull request description — pending: no pull
  request opened yet
