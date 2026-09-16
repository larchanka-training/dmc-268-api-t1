## Why

Code review of `feat/backend-architecture-plan` found twelve defects in the
persistence adapters and the domain helpers that back the review pipeline. Four
of them lose or corrupt data that the specs already require the system to keep:
findings on the old side of a diff collapse into one another, the unique
constraints meant to stop duplicates do not fire, and a run's rejection counter
is overwritten. One more can destroy a developer's database. The baseline
migration has not merged yet, so the schema can still be corrected in place
instead of through a follow-up revision.

## What Changes

Correctness of stored data:

- Deduplicate findings on the full anchor (`file_path`, `side`, `old_line`,
  `new_line`, `category`) instead of `new_line` alone. Old-side findings all
  carry `new_line = None`, so today two findings on different deleted lines of
  the same file collapse into one.
- Widen `uq_findings_anchor` to the same columns and make it
  `NULLS NOT DISTINCT`, so the database enforces what its own docstring claims.
- Make `uq_published_comments_finding` `NULLS NOT DISTINCT` so a retried
  publication cannot post a second summary comment for the same run.
- Stop `ReviewRunRepo.update` from writing `rejected_findings` back from a
  stale in-memory entity over a count the row already holds. The four outcome
  fields stay: nothing else writes them, so they are not lost updates.
- Carry those same five fields through `ReviewRunRepo.add`.
- Copy `source_branch` and `target_branch` in `MergeRequestRepo.update`, so
  retargeting a change request reaches storage.
- Give `find_by_digest` a deterministic `ORDER BY`.
- Copy the JSONB body in `context_payload_to_domain` rather than handing out the
  ORM row's dict by reference from a frozen dataclass.

Safety and contracts:

- Refuse to run the destructive test fixture against a database that is not
  explicitly a test database. `tests/conftest.py` runs
  `DROP SCHEMA public CASCADE` against `DATABASE_URL`, the variable the README
  tells a developer to export for `uvicorn` and `alembic`.
- Declare `add_validated` on the `FindingRepo` protocol, so the diff-anchor rule
  is reachable through `UnitOfWork.findings` instead of only on the concrete
  adapter.
- Escape the DSN before it passes through `configparser` in `alembic/env.py`; a
  percent-encoded password currently breaks `alembic upgrade head`.
- Replace the `assert` in `Result.unwrap` with an explicit raise, since `-O`
  strips it and the docstring promises `ValueError`.

Layering:

- Move the ASGI entrypoint from the repository root into the package, at
  `app/main.py`. The root module is the one file in the project the layering
  contracts cannot see, because `root_package = "app"` builds the graph from
  the package and a root module is not in it. Demonstrated: a root `main.py`
  importing `app.domain` and `app.infrastructure` directly leaves
  `lint-imports` reporting 2 kept, 0 broken.

Dependencies:

- Correct the comment on the `grimp<3.16` and `import-linter<2.15` caps. The
  caps stay: from 3.16 grimp's only macOS arm64 wheel is the free-threaded
  `cp314t` build, so an Apple Silicon laptop still falls back to a source
  build. The current comment says no cp314 wheel exists at all, which is wrong
  and would mislead whoever next tries to lift it.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `review-data-model`: the publication requirement currently constrains only
  findings ("A finding SHALL be published at most once per review run").
  Summary comments carry no finding, so nothing today forbids posting two of
  them for one run. Add that constraint.

## Impact

- `main.py` moves to `app/main.py`; `README.md` and
  `docs/BACKEND_ARCHITECTURE.md` name the new target
- `app/domain/dedup.py`, `app/domain/result.py`
- `app/application/ports/repositories.py`
- `app/infrastructure/db/models.py`, `repositories.py`, `mappers.py`
- `alembic/env.py`, `alembic/versions/0001_baseline_schema.py` (amended in
  place; the baseline has not merged)
- `tests/conftest.py` and the suites covering the above
- `pyproject.toml`, `uv.lock`
- Requires PostgreSQL 15 or newer for `NULLS NOT DISTINCT`. The project runs 18.
- Anyone with an existing local database rebuilt from the old baseline must
  recreate it, since the amended migration changes a constraint under the same
  revision id.
