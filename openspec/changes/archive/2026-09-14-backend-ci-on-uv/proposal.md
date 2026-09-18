## Why

The checks that keep the backend honest do not all run in CI. The integration
tests need PostgreSQL and skip without it, and `lint-imports` enforces the
layering rule and is no longer invoked. A job that is green because it skipped
the interesting part is worse than no job.

This is now measured rather than predicted. The workflow that merged with
PR #2 runs a healthy `postgres:18-alpine` service, and [run 34867309262](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34867309262)
still reported **78 passed, 33 skipped**. The same tree with the variable set
reports 110 passed. Thirty-three tests are green on paper because they did not
execute.

The cause is one word. The workflow sets `DATABASE_URL`; the suite reads
`TEST_DATABASE_URL`.

PR #2 has merged and this branch is rebased onto `develop` at `cc1b0dc`, so the
install work this change was proposed to do is done, and so is the six-file
merge it was proposed to resolve. What remains is the part PR #2 never claimed:
making the tests run, putting `lint-imports` back, and stopping an unrelated
job from deciding whether the suite executes.

Split out of `backend-architecture`, where these tasks were blocked on a file
that arrived with a different pull request. The CI workflow belongs to the
DevOps ticket's territory, so the boundary matches the tickets.

## What Changes

- Set `TEST_DATABASE_URL` in the test job, spelled `postgresql+psycopg://`.
  The suite reads that variable, not `DATABASE_URL`, and without the driver
  prefix SQLAlchemy selects psycopg2, which is not a dependency. Verified:
  `ModuleNotFoundError: No module named 'psycopg2'`.
- Assert the test job reports zero skipped, so a broken variable fails the
  build instead of producing a green run over the pure tests alone.
- Add `uv run lint-imports` back to the lint job. It was removed from
  `develop` along with the vacuous `independence` contract it was running;
  this branch restores three contracts that can actually fail, and nothing
  runs them.
- Bring `tests/test_postgres.py` onto the suite's convention. It reads
  `DATABASE_URL` and carries its own `skipif`, so it is the one test the
  zero-skipped assertion cannot cover without a second variable. Its guard
  also reads `not os.environ.get("CI")`, which on GitHub is always false, so
  an absent `DATABASE_URL` there raises `KeyError` rather than skipping.
- Stop the terraform job from gating the tests. `test` declares
  `needs: [lint, terraform]`, so an OpenTofu provider download decides whether
  the suite runs. In [run 34867309262](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34867309262) it did: the registry reset the connection, the
  test job was skipped, and a rerun of the identical commit passed.

Non-goals:

- No change to what any check asserts. Same `ruff`, same `pytest`, same
  contracts.
- No deployment, Terraform, or image work. That is DevOps ticket #12. Removing
  the `needs` edge changes when the terraform job's result is consulted, not
  what it checks.
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

**Files** — `.github/workflows/ci.yml` and `tests/test_postgres.py`.

**Depends on** — nothing outstanding. PR #2 merged into `develop` as `cc1b0dc`,
and this branch was rebased onto it, which is where the merge resolution this
change used to carry was executed.

**Team** — the workflow is `DrZeD-13`'s file from PR #2; the change was
proposed to them there before being written. Backend Base Setup (#13/#20) owns
the package-manager decision, and this follows it rather than making it.

**Risk** — turning on integration tests in CI will expose failures that are
currently invisible. That is the point, but the first run may be red. The
merge risk that used to dominate this section is gone: the rebase is done and
the merged tree passes `ruff`, `lint-imports` and 110 tests locally.
