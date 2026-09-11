## 1. Install with uv

PR #2 already installs through `setup-uv` and `uv sync --all-extras --frozen`,
so this group verifies rather than performs (design: D1).

- [ ] 1.1 Verify the run installs from `uv.lock` and not a fresh resolution: the log must name the locked versions, `grimp==3.15` among them rather than a newer one
- [ ] 1.2 Confirm the interpreter CI resolves is 3.14, by printing `uv run python --version` in the workflow
- [ ] 1.3 Verify all five places naming a Python version agree on 3.14: `requires-python`, `.python-version`, `[tool.ruff] target-version`, the workflow, and the Dockerfile's `ARG PYTHON_VERSION`

## 2. Run every check

- [ ] 2.1 Add `uv run lint-imports` as its own step in the lint job, named for what it enforces; verify a pull request with an outward import from `app.domain` fails that step and names the offending module (design: D4)
- [ ] 2.2 Keep `uv run ruff check .` as a separate step; verify a style violation fails it without touching the layering step

## 3. Make the integration tests actually run

The suite reads `TEST_DATABASE_URL`, not `DATABASE_URL`. That split arrived in
the `fix-review-backend-defects` change: the fixture that prepares the database
drops its schema, and pointing it at the variable the README tells a developer
to export for `uvicorn` would make a stray `pytest` destructive. CI has to set
the test variable, and setting only `DATABASE_URL` leaves every integration
test skipped.

- [ ] 3.1 Set `TEST_DATABASE_URL` in the test job as `postgresql+psycopg://...`, alongside the `postgres:18-alpine` service PR #2 already added; verify the prefix is present, since a bare `postgresql://` sends SQLAlchemy to psycopg2 and fails with `ModuleNotFoundError` (design: D3)
- [ ] 3.2 Assert the suite is not silently skipping: the test step must report 0 skipped, so a broken `TEST_DATABASE_URL` fails the build rather than producing a green run over the pure tests alone (design: D3)
- [ ] 3.3 Prove the assertion bites: temporarily point `TEST_DATABASE_URL` at a database that does not exist, verify the job goes red rather than green-with-skips, then revert
- [ ] 3.4 Verify the test role can create a role: `tests/db/test_alembic_env.py` provisions one to exercise a percent-encoded password, so a non-superuser CI role would fail that test

## 4. One dependency list

Both requirements files are already gone: `requirements.txt` in the
backend-architecture change, `requirements-dev.txt` on PR #2. This group
confirms nothing was left pointing at them (design: D2).

- [ ] 4.1 Confirm nothing reads either file: `grep -rn "requirements" .github/ README.md Dockerfile` returns nothing
- [ ] 4.2 Verify a fresh clone plus `uv sync --all-extras` runs the full suite
- [ ] 4.3 Verify the README install section names only `uv sync` and works when followed literally in a clean clone

## 5. Merge with PR #2

`git merge-tree` names six conflicting files. Every one resolves toward this
branch (design: D5).

- [ ] 5.1 Resolve `app/main.py` in favour of the thin entrypoint calling `create_app()`, discarding PR #2's inline routes; verify `uvicorn app.main:app` serves `/` and `/health` and that `app.state.container` exists, which the inline version has no way to provide
- [ ] 5.2 Rewrite `tests/test_health.py` as a `TestClient` call against `/health` (design: D6); verify it fails when the route's path in the decorator is changed, which the direct-function version did not
- [ ] 5.3 Resolve `pyproject.toml` toward this branch in all four places: `requires-python`, dev dependencies, the `integration` marker text, and `[tool.importlinter]`; verify `uv run lint-imports` reports three contracts kept, not the single `independence` contract from PR #2
- [ ] 5.4 Resolve `.gitignore`, `README.md` and `uv.lock`; verify `uv sync --all-extras --frozen` succeeds against the merged lock and `git status` is clean afterwards
- [ ] 5.5 Verify the merged tree passes `uv run ruff check .`, `uv run lint-imports` and the full suite with `TEST_DATABASE_URL` set

## 6. Close out

- [ ] 6.1 Verify the whole workflow green on a pull request, with the test job reporting 0 skipped and the lint job showing both `ruff` and `lint-imports` as separate passing steps
- [ ] 6.2 Open the PR referencing DevOps ticket #12 and the discussion on PR #2, and ask `DrZeD-13` to review, since the workflow is their file
