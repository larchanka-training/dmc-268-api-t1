## Context

See `proposal.md` for motivation. What shapes the approach:

- The baseline migration `0001_baseline_schema` has not merged. Nothing in
  production or in a teammate's branch depends on its current constraint
  definitions, so the schema can still be corrected under the same revision id.
- `DiffAnchor` carries `side`, `old_line` and `new_line`, and only one of the
  two line numbers is populated: `validate_anchor` reads `new_line` for
  `DiffSide.NEW` and `old_line` for `DiffSide.OLD`. Every old-side anchor
  therefore has `new_line = None`.
- PostgreSQL treats NULLs as distinct in a unique constraint unless told
  otherwise. `NULLS NOT DISTINCT` arrived in PostgreSQL 15; the project targets
  18.
- SQLAlchemy 2.0.52 emits `UNIQUE NULLS NOT DISTINCT (...)` from
  `postgresql_nulls_not_distinct=True` on a `UniqueConstraint`. Verified against
  the installed version rather than assumed.
- `tests/conftest.py` reads the same `DATABASE_URL` the service and `alembic`
  read, and drops the schema on it.

## Goals / Non-Goals

**Goals:**

- A duplicate finding is refused by the database whichever side of the diff it
  sits on, and the domain and the schema agree on what "duplicate" means.
- A repository write cannot silently undo a counter another write already
  incremented.
- Running the test suite cannot destroy a database a developer is using for
  anything else.

**Non-Goals:**

- Changing what a review run does, how findings are produced, or how comments
  are published. Only storage and the helpers around it are in scope.
- Introducing a second migration revision. See D1.
- Reworking the `Result` type. Only `unwrap`'s failure path changes.

## Decisions

### D1 — Amend `0001_baseline_schema` rather than add a revision

The constraint corrections go into the existing baseline, under the same
revision id.

*Why:* the baseline is unmerged, so a follow-up revision would exist only to
undo a mistake that never reached anyone. A single clean baseline is also what
the next person reads when they want to know the schema.

*Alternative considered:* a `0002` revision that drops and recreates both
constraints. Rejected while the baseline is unmerged; it becomes the only
option the moment it merges.

*Consequence:* anyone who already ran `alembic upgrade head` from this branch
has a database whose schema no longer matches the revision it claims. They have
to rebuild it. Task 6.1 says so in the README.

### D2 — `NULLS NOT DISTINCT` rather than a coalesced expression index

Both unique constraints get `postgresql_nulls_not_distinct=True`.

*Why:* it says exactly what is meant, and it keeps the rule a constraint rather
than an index that happens to be unique. The intent stays readable in
`models.py` instead of being encoded in a `COALESCE` sentinel.

*Alternative considered:* a unique index over
`COALESCE(new_line, -1), COALESCE(old_line, -1)`. Rejected because it needs a
sentinel value that is not a line number, and every reader has to work out why
`-1` is there.

*Trade-off:* requires PostgreSQL 15. The project runs 18 in development, CI and
Terraform, so this costs nothing today, and it is recorded here so a future
downgrade is a deliberate decision rather than a surprise.

### D3 — One anchor tuple, used by both the domain and the schema

`deduplicate` keys on `(file_path, side, old_line, new_line, category)` and
`uq_findings_anchor` covers the same columns.

*Why:* the domain deduplicates so the caller learns what was dropped, and the
database deduplicates so no insertion path can forget. Those two only stay
honest if they mean the same thing by "the same finding". Today they do not,
and both are wrong in the same direction.

*Alternative considered:* keying on `side` plus whichever line number that side
uses. Rejected because it puts the same conditional in two places; carrying both
columns is simpler and the unused one is always `None`.

### D4 — A field with two writers belongs to the row, not to the entity

`ReviewRunRepo.update` stops assigning `rejected_findings` from the entity. It
keeps writing `failure_reason`, `model`, `tokens_used` and `duration_seconds`.
`add` starts carrying all five, because at insert time the entity is the only
source.

*Why:* `add_validated` increments `rejected_findings` on the row without the
caller's `ReviewRun` ever hearing about it, so a later `update` from that stale
entity resets the counter to whatever it was when the entity was loaded. The
rule that avoids this class of bug is that a field has one writer.

*Scope correction:* the review that raised this listed the four outcome fields
as suffering the same lost update. They do not. Nothing but the caller writes
them, and a run is inserted while still queued, knowing none of them, so
removing them from `update` would leave no path that ever persists a model name
or a token count. Only `rejected_findings` has a second writer, so only it
moves.

*Alternative considered:* refreshing the entity after `add_validated`, or
returning the new count. Rejected: it makes every caller responsible for
remembering to re-read, which is the same bug waiting for the next caller.

### D5 — A separate variable for the test database

The destructive fixture reads `TEST_DATABASE_URL`. It does not fall back to
`DATABASE_URL`.

*Why:* the README tells a developer to export `DATABASE_URL` for `uvicorn` and
`alembic`. A fixture that drops the schema on whatever that variable points at
turns a normal working shell into a loaded gun. Requiring a different name means
the destructive path can only be reached on purpose.

*Alternative considered:* keeping `DATABASE_URL` and refusing to run unless the
database name matches something like `*_test`. Rejected as a weaker guard that
still fails for anyone whose development database happens to match, and it
teaches the reader that the suite inspects names, which is more surprising than
a second variable.

*Consequence:* CI and the README both need the new variable. The README is task
5.2 here. CI is not: no workflow exists on this branch, `.github/` arrives with
PR #2, so the wiring belongs to `backend-ci-on-uv`, which owns that file and
lands after #2 merges. Its task 3.1 carries it.

*Ordering risk:* between this change merging and that one, a CI job that sets
only `DATABASE_URL` runs the pure tests and skips every integration test while
staying green. `backend-ci-on-uv` task 3.2 is what closes that window, by
failing the build on any skip.

### D6 — `add_validated` belongs on the protocol

`FindingRepo` declares `add_validated`; `add` stays for the paths that have
already validated.

*Why:* the diff-anchor rule is a requirement of the spec, and right now the only
method that enforces it is invisible to anything holding a `FindingRepo`. A
guard reachable only by knowing the concrete class is not a guard.

*Alternative considered:* removing `add` from the protocol so validation is the
only option. Rejected: the mapper path and any future backfill legitimately
insert findings whose hunks are not at hand, and forcing them through a
validating call would mean inventing hunks to satisfy it.

### D7 — Escape the DSN at the `configparser` boundary

`alembic/env.py` writes `settings.database_url.replace("%", "%%")` into the ini
option.

*Why:* `configparser` treats `%` as interpolation and validates on write, so
`set_main_option` raises `ValueError: invalid interpolation syntax` for a
percent-encoded password before anything connects. Escaping at the one place
the value enters the ini keeps both online and offline modes working, because
`get_main_option` un-escapes on the way out.

*Alternative considered:* bypassing the ini and passing the URL straight to
`create_engine`. Rejected because offline mode reads the option too, so it would
need its own path, and the settings object would then be read in two places.

### D8 — Keep the `grimp` cap, and say accurately why

The cap stays at `grimp<3.16` and `import-linter>=2.0,<2.15`. Only the comment
changes.

*Why:* the change set out to lift it, on the reading that its stated reason had
expired. Checking rather than assuming showed otherwise. The old comment says
3.16 and 3.17 "ship no cp314 wheel", which is not true: they ship cp314 wheels
for Linux and Windows. What they do not ship, from 3.16 onward, is a macOS
arm64 wheel for the ordinary interpreter. The only macOS arm64 build is
`cp314t`, the free-threaded one. So on an Apple Silicon laptop `uv sync` still
falls back to maturin and a Rust toolchain, while CI on Linux would be fine.

*Consequence:* the cap is a developer-machine constraint, not a CI one, and the
comment now says that. Lifting it becomes correct when grimp publishes a
regular cp314 macOS arm64 wheel, and the comment names the condition to check.

*Alternative considered:* lifting the cap and letting macOS developers build
from source. Rejected: it costs a Rust toolchain on every laptop to gain a
newer graph builder nobody is waiting for.

### D9 — The entrypoint moves inside the package

`main.py` becomes `app/main.py`. The root module goes away.

*Why:* the layering contracts are declared over `root_package = "app"`, so
grimp builds its graph from the package and never sees a module at the root.
That makes the root entrypoint the single file where the dependency rule does
not apply. It is three lines today, and nothing stops the next person adding a
session or a repository call to it: `lint-imports` reports 2 kept, 0 broken
either way. Verified by temporarily importing `app.domain.entities` and
`app.infrastructure.db.models` from the root module and watching the contracts
pass.

*The move alone is not enough.* Inside the package the module is analysed, but
no contract constrains it until it is named in one: it is a sibling of the
layers, not a member. So `app.main` joins the layers contract as the topmost
layer, which stops anything below importing it, and a second contract forbids
it importing `app.domain`, `app.application` or `app.infrastructure` directly.
That one carries `allow_indirect_imports`, because reaching infrastructure
through the factory is exactly what the entrypoint is for. With both in place,
the same import that was invisible at the root now breaks the build.

*This reverses an earlier decision.* The archived backend-architecture change
kept the entrypoint at the root so `uvicorn main:app` and PR #2's CI would keep
working. That reasoning has since inverted: PR #2 moves the entrypoint into the
package itself, as `app/main.py`, and its Dockerfile both copies only `app/`
and runs `uvicorn app.main:app`. Staying at the root now means the two branches
merge without a conflict and leave two competing applications, with the image
serving the one that has no settings and no container.

*Alternative considered:* keeping the root module and adding a second
import-linter contract rooted at the repository. Rejected: grimp takes a
package, and making the repository root a package to satisfy a linter is a
larger change than moving three lines.

## Risks / Trade-offs

- **A developer already applied the old baseline** → their schema silently
  disagrees with revision `0001`. Mitigated by saying so in the README and by
  D5, which forces a separate test database anyway, so the rebuild is cheap.

- **`NULLS NOT DISTINCT` ties the schema to PostgreSQL 15+** → mitigated by the
  project running 18 everywhere, and recorded in D2 so a downgrade is a
  decision.

- **Widening `uq_findings_anchor` rejects writes the old constraint accepted** →
  those writes were the duplicates the spec asks to collapse, and there is no
  stored data to migrate.

- **Callers relying on `update` to persist `model` or `tokens_used`** → nothing
  does today; the fields are written at insert. A test pins the new contract so
  a future caller finds out at review time rather than in production.

## Migration Plan

1. Amend `0001_baseline_schema` and rebuild local databases from scratch
   (`DROP SCHEMA public CASCADE` then `alembic upgrade head`, which is what the
   test fixture already does against `TEST_DATABASE_URL`).
2. Set `TEST_DATABASE_URL` in CI alongside the existing `DATABASE_URL`.
3. No rollback step: the change is unreleased, and reverting the branch reverts
   the schema with it.
