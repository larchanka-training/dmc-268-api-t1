## Context

See `proposal.md` — Why. What shapes the approach:

- `ci.yml` on `develop` runs four jobs (`syntax`, `lint`, `terraform`,
  `test`) on `ubuntu-latest`, installing through `astral-sh/setup-uv` pinned at
  `0.12.12` with `uv sync --all-extras --frozen`. Both requirements files are
  gone and `.gitlab-ci.yml` was removed. D1 and D2 below describe decisions
  PR #2's author implemented; they are kept as the record of why, and their
  tasks are now verification.
- `lint-imports` is not in the workflow. It was there on PR #2's branch and was
  removed in review together with the `independence` contract it ran, which
  compared one module against nothing and could not fail. This branch restores
  three contracts that can, so the step has to come back with them.
- The test job runs a `postgres:18-alpine` service with a health check and
  sets `DATABASE_URL`. The suite reads `TEST_DATABASE_URL`, so every
  integration test still skips: 78 passed, 33 skipped in [run 34867309262](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34867309262), against 110
  passed for the same tree run with the variable set.
- `test` declares `needs: [lint, terraform]`, so the suite does not run until
  OpenTofu has downloaded a provider from a third-party registry.
- A DSN reaches SQLAlchemy as well as `psycopg.connect`. Without the
  `+psycopg` prefix SQLAlchemy picks its default driver, psycopg2, which is
  not installed. Confirmed against the installed version rather than assumed.
- The two branches collided in six files, and the rebase onto `cc1b0dc`
  resolved them: `.gitignore`, `README.md`, `app/main.py`, `pyproject.toml`,
  `tests/test_health.py` and `uv.lock`. D5 and D6 are the record of how, and
  their tasks are done.
- The team confirmed uv as the package manager.

## Goals / Non-Goals

**Goals:**

- Every check that exists runs in CI, and a skipped test cannot be mistaken for
  a passing one.
- One dependency list.
- A failing check names what broke without needing a local reproduction.

**Non-Goals:**

- Changing what any check asserts.
- Caching, matrix builds, or parallelism. Worth doing when the run is slow
  enough to notice; it is not.
- Deployment or image building.

## Decisions

### D1 — `uv sync` from the lockfile, not `pip install` from a list

CI installs with `astral-sh/setup-uv` and `uv sync --all-extras`.

*Why:* the lockfile pins exact versions, so CI installs what a developer
installed rather than whatever the index offers that morning. This is not
hypothetical here: `grimp` is capped in `pyproject.toml` because a newer
release ships no cp314 wheel and falls back to a Rust build, and a fresh
resolution is exactly how that gets picked up again.

*Alternative considered:* keeping pip and generating `requirements*.txt` from
the lock in CI. Rejected: a generated file that has to stay in step is the
drift problem again, one step removed.

### D2 — Delete the requirements files rather than keep them in step

`requirements.txt` is already deleted. `requirements-dev.txt` goes once the
workflow no longer reads it, and the README points only at `uv sync`.

*Why:* they have already drifted once, silently, and the failure mode is a
teammate following the README into a broken install. Two lists that must agree
will disagree.

*Trade-off:* someone without uv loses the pip path. Acceptable, because uv
installs the pinned interpreter too, and the alternative is maintaining a
second list by hand.

### D3 — PostgreSQL as a service container, and prove it is being used

A `postgres:18-alpine` service with a health check, and `DATABASE_URL` in the
job environment.

The subtlety is that adding the service is not enough to know it worked. A
misconfigured `DATABASE_URL` leaves the tests skipping and the job green,
which is the failure this change exists to remove. So CI asserts the count:
`pytest` must report zero skipped in the integration job.

*Why a service container over `docker run`:* GitHub waits for the health check
before the steps start, so there is no sleep-and-hope in the workflow.

### D4 — `lint-imports` as its own step

Not folded into the lint job's `ruff` step.

*Why:* a combined step reports one failure for two unrelated problems, and the
layering rule is the one most likely to be broken by someone who does not know
it exists yet. A step named for it is a better error message than a diff in a
log.

*What changed:* the step existed on PR #2's branch and was removed before the
merge, correctly — the contract behind it was an `independence` contract over a
single module, which has nothing to compare and therefore always passes.
Deleting a check that cannot fail is right; the mistake would be to conclude
the step itself was the problem. This branch brings three contracts that fail
when seeded with a violation, so the step returns with something to enforce.

### D5 — The merge resolves toward this branch, and `app/main.py` is the one that matters

*Executed.* PR #2 merged as `cc1b0dc` and this branch was rebased onto it.
Every conflicting file took this branch's side, with one deliberate exception:
`.gitignore`, where the two sides add different entries and nothing conflicts
in substance, so the resolution is their union. `README.md` took this branch's
side but kept the one fact the other side carried alone — that `infra/` brings
up PostgreSQL, RabbitMQ and the API container through Terraform.

`app/main.py` is the reason to be careful. PR #2 defines a `FastAPI` there with
its routes inline; this branch defines a three-line entrypoint calling
`create_app()`, which loads settings and builds the container. Both Dockerfiles
and both workflows point at `app.main:app`, so taking the wrong side produces a
service that starts, answers `/health`, and has no database wiring at all.
Taking this branch's side needs no change on PR #2's part: its
`CMD ["uvicorn", "app.main:app"]` keeps working.

That the conflict exists at all is the intended result of moving the entrypoint
into the package. While it sat at the repository root the two branches added
different files, merged silently, and left two competing applications.

`pyproject.toml` disagrees in four places: `requires-python` (`>=3.14` against
`==3.14.*`), the dev dependency list, the `integration` marker text, and
`[tool.importlinter]`, where PR #2 carries a single `independence` contract over
one module. An independence contract needs at least two modules to compare, so
that one can never fail; this branch's three contracts replace it.

### D6 — `tests/test_health.py` is rewritten, not merged

*Executed.* It is a `TestClient` call against `/health`, and it was checked the
way the decision argues for: changing the path in the route decorator turns the
test red with `assert 404 == 200`. The version it replaced stayed green under
that change.

*Why:* PR #2's version does `from app.main import health_check` and asserts on
the return value. After the merge that name lives inside `create_app`, so the
import breaks and the job goes red. Repointing the import would restore a test
that never touched the route: change the path in the decorator and it still
passes. Going through the client tests what the service actually exposes.

### D7 — A test job does not wait on infrastructure it does not use

`test` drops `terraform` from its `needs` and keeps `lint`.

*Why:* the jobs check unrelated things. `tofu validate` says the Terraform is
well-formed; `pytest` says the service works. Chaining them means every
dependency of the first becomes a dependency of the second, including a
provider download from `registry.opentofu.org`. In [run 34867309262](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/34867309262) that registry
reset the connection, `terraform` went red, and `test` reported `skipped` —
indistinguishable in the checks list from a job that had nothing to run. A
rerun of the identical commit went green, which is the signature of a flake
gating something it has no relationship with.

*Why keep `needs: lint`:* that one is ordering for its own sake. A failing
`ruff` run is cheap and immediate, and there is no point spending a database
container on a branch that does not lint.

*The same reasoning applies to `tests/test_postgres.py`.* It reads
`DATABASE_URL` and carries its own `skipif` instead of the shared `requires_db`
marker, which is why the suite still reports one skip when everything else runs.
Its guard is `not os.environ.get("DATABASE_URL") and not os.environ.get("CI")`,
and on GitHub `CI` is always set — so the guard is false, the test body runs,
and `os.environ["DATABASE_URL"]` raises `KeyError` on any runner that does not
set it. An escape hatch that converts a skip into a crash in exactly the
environment it was written for belongs to the same family of problem as the
zero-skipped assertion in D3: both are about a test result meaning what it says.

## Risks / Trade-offs

- **The first run with integration tests enabled may be red.** → That is the
  change working. Anything it finds was already broken and invisible.
- **CI gets slower.** → A database container costs seconds. If it becomes a
  problem, split the integration job so the fast checks still fail fast.
- **Editing another author's workflow.** → Proposed on PR #2 first. If they
  would rather do it themselves, this change becomes a review instead.
- **Unchaining the terraform job loses the guarantee that a broken `infra/`
  blocks a merge.** → It does not: the job still runs on every pull request and
  still fails the workflow. What it stops doing is deciding whether a different
  job runs at all.
- **Dropping the pip path shuts out anyone without uv.** → uv is a single
  binary and installs its own interpreter. The README carries the one command.

## Migration Plan

1. ~~Land PR #2, then resolve the merge into this branch~~ — done, rebased
   onto `cc1b0dc` (D5), including the `TestClient` rewrite (D6).
2. Add `TEST_DATABASE_URL` to the test job with the `+psycopg` prefix, and the
   assertion that nothing skipped.
3. Put `uv run lint-imports` back in the lint job, as its own step (D4).
4. Fold `tests/test_postgres.py` into the suite's convention and drop its `CI`
   escape hatch (D7).
5. Remove `terraform` from the test job's `needs` (D7).
6. Verify on a pull request that the test job reports 0 skipped and that both
   lint steps pass separately.

**Rollback:** revert the workflow file. Nothing else depends on it.

## Open Questions

- Whether `pylint` joins the pipeline. It is in the team's stack and is not
  configured in the repository yet; that is Base Setup's call.
