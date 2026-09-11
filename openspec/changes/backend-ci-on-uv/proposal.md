## Why

The checks that keep the backend honest do not all run in CI. `lint-imports`
enforces the layering rule and is never invoked; the integration tests need
PostgreSQL and skip without it, so 22 of 87 tests would pass silently by not
running. A job that is green because it skipped the interesting part is worse
than no job.

PR #2 has moved since this was written. Its workflow now installs through
`astral-sh/setup-uv` pinned at `0.12.12` and `uv sync --all-extras --frozen`,
both requirements files are gone, and the test job runs a PostgreSQL service
with a health check. The install work this change was proposed to do has been
done by its author instead, so what remains here is to verify it and to finish
what the review of that branch surfaced.

The tests are the part still not honest. They read `TEST_DATABASE_URL`, which
the workflow does not set, so every integration test skips and the job stays
green over the pure tests alone.

Split out of `backend-architecture`, where these tasks were blocked on a file
that arrives with a different pull request. The CI workflow belongs to the
DevOps ticket's territory, so the boundary now matches the tickets.

## What Changes

- Set `TEST_DATABASE_URL` in the test job, spelled `postgresql+psycopg://`.
  The suite reads that variable, not `DATABASE_URL`, and without the driver
  prefix SQLAlchemy selects psycopg2, which is not a dependency. Verified:
  `ModuleNotFoundError: No module named 'psycopg2'`.
- Assert the test job reports zero skipped, so a broken variable fails the
  build instead of producing a green run over the pure tests.
- Verify what PR #2 already built: install from the lockfile, `lint-imports`
  as its own step, one dependency list, 3.14 everywhere CI names a version.
- Resolve the merge with PR #2, whose branch collides with this one in six
  files. `app/main.py` is an add/add conflict between its inline routes and
  our thin entrypoint; `tests/test_health.py` imports a function that moves
  inside the factory; `pyproject.toml` disagrees in four places.
- Rewrite `tests/test_health.py` through `TestClient`, so it exercises the
  route rather than calling the handler directly.

Non-goals:

- No change to what any check asserts. Same `ruff`, same `pytest`, same
  contracts.
- No deployment, Terraform, or image work. That is DevOps ticket #12.
- No new tests. This is about running the ones that exist.

## Capabilities

### New Capabilities

None. This changes how existing checks are executed, not what the system does.
The requirements it serves are already specified: "Violation is caught
mechanically" in `backend-architecture`, and "Drift fails the check" in
`database-migrations`. `skip_specs: true` is set for that reason.

### Modified Capabilities

None.

## Impact

**Files** — `.github/workflows/ci.yml`, and the merge resolution across
`app/main.py`, `tests/test_health.py`, `pyproject.toml`, `README.md`,
`.gitignore` and `uv.lock`.

**Depends on** — PR #2 merging first. The workflow arrives with it, and so do
the files this change has to reconcile.

**Team** — the workflow is `DrZeD-13`'s work on PR #2; the change was proposed
to him there before being written. Backend Base Setup (#13/#20) owns the
package-manager decision, and this follows it rather than making it.

**Risk** — turning on integration tests in CI will expose failures that are
currently invisible. That is the point, but the first run may be red. The
sharper risk is now the merge: six files conflict, and the one that matters is
`app/main.py`, where taking the wrong side leaves the image serving an
application with no settings and no container.
