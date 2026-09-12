## Why

A review of `feat/backend-architecture-plan` found seven things. Four are the
architecture document and the ERD describing a system that no longer exists:
the previous change moved the test suite to `TEST_DATABASE_URL`, widened the
findings constraint and added a third layering contract, and none of that
reached the documents. Those documents are the ticket's deliverable, so a
reader who trusts them is misled about the exact rules the change was made to
fix. `docs/erd.md` still prints the constraint in the shape that was the bug.

The other three are small and independent: a connection pool that will hand
out a dead connection after an idle period, an import hidden inside a
function, and a test condition whose branches are identical.

## What Changes

Documents brought back in step with the code:

- `docs/erd.md` prints the findings constraint as it is declared, with `side`,
  `old_line` and `NULLS NOT DISTINCT`, and the published-comment constraint
  likewise.
- `docs/BACKEND_ARCHITECTURE.md` names three layering contracts and shows the
  matching `lint-imports` output.
- The same document stops naming exact test counts and points at
  `TEST_DATABASE_URL` rather than `DATABASE_URL`.

Two cheap checks so points above cannot silently return:

- A test comparing the constraint column lists in `docs/erd.md` against the
  DDL SQLAlchemy compiles from the models.
- A test comparing the number of contracts the architecture document claims
  against the number declared in `pyproject.toml`.

Defects:

- `create_engine` gets `pool_pre_ping=True`, so a connection that died while
  idle is discovered and replaced instead of surfacing as `OperationalError`
  on the next request.
- `from dataclasses import replace` moves to the top of `app/domain/lifecycle.py`.
- The no-op ternary in `tests/test_container.py` becomes the assignment it
  already was.
- The `TRUNCATE` in `tests/conftest.py` builds its table list from
  `Base.metadata` instead of repeating six names, so a table added later
  cannot be left out and let one test see another's rows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. Nothing here changes what the system does: the constraint, the layering
rule and the environment variable are already what the specs and the code say.
Only the documents describing them, one engine argument and two lines of test
code change. `skip_specs: true` is set for that reason.

## Impact

- `docs/erd.md`, `docs/BACKEND_ARCHITECTURE.md`
- `app/infrastructure/container.py`, `app/domain/lifecycle.py`
- `tests/conftest.py`, `tests/test_container.py`, and two new documentation
  tests
- No schema change, no migration, no API change.
