## 1. Install with uv

The workflow on `develop` installs through `setup-uv` and `uv sync
--all-extras --frozen`, so this group verifies rather than performs
(design: D1).

- [x] 1.1 Verify the run installs from `uv.lock` and not a fresh resolution: the log must name the locked versions, `grimp==3.15` among them rather than a newer one — confirmed, `grimp==3.15` and `import-linter==2.14`
- [x] 1.2 Confirm the interpreter CI resolves is 3.14. No print step was added: the run already proves it twice, through `UV_PYTHON: 3.14` in the setup-uv environment and `.venv/lib/python3.14/...` in the paths pytest reports. A step that exists to restate what the log already says is noise in someone else's workflow
- [x] 1.3 Verify all five places naming a Python version agree on 3.14: `requires-python`, `.python-version`, `[tool.ruff] target-version`, the workflow, and the Dockerfile's `ARG PYTHON_VERSION`

## 2. Run every check

`lint-imports` is no longer in the workflow. It was removed from `develop`
together with the vacuous `independence` contract it was running, so 2.1 adds
the step back rather than verifying it. The three contracts this branch
restores are real ones, and they pass (design: D4).

- [x] 2.1 Add `uv run lint-imports` to the lint job as its own step, named for what it enforces; verify a pull request with an outward import from `app.domain` fails that step and names the offending module (design: D4) — the step runs and reports 3 kept, and a seeded `import sqlalchemy` in `app/domain/entities.py` breaks "Inner layers know no vendor" with `app.domain.entities -> sqlalchemy (l.151)`
- [x] 2.2 Keep `uv run ruff check .` as a separate step; verify a style violation fails it without touching the layering step — an unused `import os` fails ruff with `F401` while `lint-imports` exits 0 on the same tree

## 3. Make the integration tests actually run

The suite reads `TEST_DATABASE_URL`, not `DATABASE_URL`. That split arrived in
the `fix-review-backend-defects` change: the fixture that prepares the database
drops its schema, and pointing it at the variable the README tells a developer
to export for `uvicorn` would make a stray `pytest` destructive. CI has to set
the test variable, and setting only `DATABASE_URL` leaves every integration
test skipped.

Measured, not predicted: [run 34867309262](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34867309262) reported **78 passed, 33
skipped** with the PostgreSQL service up and healthy.

- [x] 3.1 Set `TEST_DATABASE_URL` in the test job as `postgresql+psycopg://...`, alongside the `postgres:18-alpine` service already in the workflow; verify the prefix is present, since a bare `postgresql://` sends SQLAlchemy to psycopg2 and fails with `ModuleNotFoundError` (design: D3)
- [x] 3.2 Assert the suite is not silently skipping: the test step must report 0 skipped, so a broken `TEST_DATABASE_URL` fails the build rather than producing a green run over the pure tests alone (design: D3)
- [x] 3.3 Prove the assertion bites. Done by removing the variable rather than pointing it at a database that does not exist: a bad DSN reddens the build through connection failures and never reaches the assertion, so it would prove psycopg works, not this check. [run 34870118429](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34870118429): `77 passed, 34 skipped` followed by `::error::a test skipped, so this run proves less than it appears to`, job red. Reverted straight after, and [the run after it](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34870389897) is green
- [x] 3.4 Verify the test role can create a role: `tests/db/test_alembic_env.py` provisions one to exercise a percent-encoded password, so a non-superuser CI role would fail that test — it passed in CI, where `POSTGRES_USER` is the instance superuser
- [x] 3.5 Bring `tests/test_postgres.py` onto the suite's convention: it reads `DATABASE_URL` and skips on its own `skipif` rather than the shared `requires_db`, so it is the one test the 0-skipped assertion cannot cover without a second variable (design: D7)
- [x] 3.6 Remove the `and not os.environ.get("CI")` escape from that skipif: on GitHub `CI` is always set, so the guard evaluates false and the body raises `KeyError: 'DATABASE_URL'` instead of skipping whenever the variable is absent (design: D7)

## 4. One dependency list

Both requirements files are gone: `requirements.txt` in the
backend-architecture change, `requirements-dev.txt` on PR #2. This group
confirms nothing was left pointing at them (design: D2).

- [x] 4.1 Confirm nothing reads either file: `grep -rn "requirements" .github/ README.md Dockerfile` returns nothing
- [x] 4.2 Verify a fresh clone plus `uv sync --all-extras` runs the full suite — 111 passed against a live database, 77 passed / 34 skipped without one
- [x] 4.3 Verify the README install section names only `uv sync` and works when followed literally in a clean clone — the Setup section is the single command `uv sync --all-extras`, and it is the only install path the README names

## 5. Merge with PR #2

Done by rebasing this branch onto `develop` at `cc1b0dc` once PR #2 landed.
Six files conflicted, every one resolved toward this branch except
`.gitignore`, where the two sides are additive and the resolution is their
union (design: D5).

- [x] 5.1 Resolve `app/main.py` in favour of the thin entrypoint calling `create_app()`, discarding PR #2's inline routes; verify `uvicorn app.main:app` serves `/` and `/health` and that `app.state.container` exists, which the inline version has no way to provide
- [x] 5.2 Rewrite `tests/test_health.py` as a `TestClient` call against `/health` (design: D6); verify it fails when the route's path in the decorator is changed, which the direct-function version did not — confirmed, the mutated route gives `assert 404 == 200`
- [x] 5.3 Resolve `pyproject.toml` toward this branch in all four places: `requires-python`, dev dependencies, the `integration` marker text, and `[tool.importlinter]`; verify `uv run lint-imports` reports three contracts kept, not the single `independence` contract from PR #2
- [x] 5.4 Resolve `.gitignore`, `README.md` and `uv.lock`; verify `uv sync --all-extras --frozen` succeeds against the merged lock and `git status` is clean afterwards
- [x] 5.5 Verify the merged tree passes `uv run ruff check .`, `uv run lint-imports` and the full suite with `TEST_DATABASE_URL` set — 110 passed, 1 skipped, the skip being `tests/test_postgres.py` (3.5)

## 6. Stop unrelated infrastructure from gating the tests

`test` declares `needs: [lint, terraform]`, so a provider download decides
whether the suite runs at all. This is not hypothetical: [its first attempt](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34867309262/attempts/1) went red on `could not connect to registry.opentofu.org: connection
reset by peer`, the test job was skipped, and a rerun of the same commit passed
untouched (design: D7).

- [x] 6.1 Drop `terraform` from the test job's `needs`; verify that a failing `tofu validate` still fails the workflow — in [run 34870118429](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34870118429) terraform failed with `Reference to undeclared input variable` and the workflow was red. Note the independence is shown behaviourally, not by wall clock: on that run terraform finished at 16:41:28 and test started at 16:41:31, so the two did not actually overlap, and the proof is 6.2 rather than any timing
- [x] 6.2 Verify a red terraform job no longer leaves the test job reporting `skipped` — in [run 34870118429](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34870118429) terraform was `failure` and test was `failure` on its own reason, having run to completion. Under the old `needs: [lint, terraform]` it would have reported `skipped`

## 7. Close out

- [x] 7.1 Verify the whole workflow green on a pull request, with the test job reporting 0 skipped and the lint job showing both `ruff` and `lint-imports` as separate passing steps — [run 34869407903](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34869407903): four jobs green, `111 passed` with no skips, both lint steps separate
- [x] 7.2 Reference DevOps ticket #12 and the discussion on PR #2. Not a PR of its own: the work landed in PR #5, because splitting it would have merged three import-linter contracts and 33 integration tests that CI never ran. #5's description now carries the CI section, the ticket reference and the link to the #2 comment. No review was requested from `DrZeD-13` — they are already commenting on #5, and the description points at the workflow diff
