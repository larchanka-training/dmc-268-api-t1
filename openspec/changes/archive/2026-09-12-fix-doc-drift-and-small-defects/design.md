## Context

See `proposal.md` for motivation. What shapes the approach:

- Three of the four document defects were introduced by the previous change,
  not by the original writing. It moved the suite to `TEST_DATABASE_URL`,
  widened `uq_findings_anchor` and added the entrypoint contract, and the
  documents were not touched. So the question is not only how to correct them
  but which of them will drift again.
- The facts differ in how stable they are. A column list and a contract count
  are structural: they exist in code in a form a test can read. A test count
  changes whenever anyone adds a test, and no test that asserts it can be
  anything but a nuisance.
- `docs/erd.md` states each rule twice over: prose plus a table of constraint
  definitions. The table is the part that can be compared against the schema.
- `tests/conftest.py` already imports nothing from the models; the `TRUNCATE`
  list is a literal string.

## Goals / Non-Goals

**Goals:**

- The two documents describe the constraints, contracts and environment
  variable the code actually uses.
- The facts that can drift again and are cheap to check are checked.
- A stale connection is retried rather than raised.

**Non-Goals:**

- Rewriting the documents. Only the statements that are wrong change.
- Testing documentation prose. See D2: only structural facts get a test.
- Any change to the schema, the specs or the API.

## Decisions

### D1 — Remove the test counts rather than correct them

`docs/BACKEND_ARCHITECTURE.md` stops saying "runs 65 tests; the 22 that need
PostgreSQL". It says the suite runs without a database and that the tests
which need one are marked `integration` and skip unless `TEST_DATABASE_URL` is
set.

*Why:* the number was wrong within a week of being written, and correcting it
to 74 and 107 only resets the clock. What the paragraph is actually telling the
reader is that the domain is testable without infrastructure, and that survives
every new test. The README keeps the counts, because that is where someone
checks what they should expect to see.

*Alternative considered:* a test asserting the documented counts. Rejected: it
turns adding a test into a two-file edit and teaches people to distrust the
suite.

### D2 — Test the structural facts, and only those

Two tests:

- The constraint table in `docs/erd.md` against the DDL SQLAlchemy compiles
  from `models.py`, comparing the column lists and the `NULLS NOT DISTINCT`
  marker.
- The number of contracts the architecture document claims against the number
  of `[[tool.importlinter.contracts]]` entries in `pyproject.toml`.

*Why:* both are facts that exist in machine-readable form on the code side, so
the comparison is exact rather than a text match against prose. Both are also
exactly the facts that just drifted.

*Alternative considered:* generating `docs/erd.md` from the models. Rejected:
the document carries prose the models cannot produce, and a half-generated file
invites someone to edit the generated half.

*Trade-off:* the tests read the documents by parsing a markdown table and a
sentence. That is brittle to reformatting. Both fail loudly with the file and
the expected shape named, so a reformat produces a clear failure rather than a
silent pass, which is the acceptable direction to be wrong in.

### D3 — `pool_pre_ping` rather than a recycle timeout

`create_engine(..., pool_pre_ping=True)`.

*Why:* it asks the connection whether it is alive before handing it out, so it
covers every reason a connection died: server restart, idle timeout, NAT
dropping the flow. A `pool_recycle` timeout only covers connections older than
a number someone guessed.

*Trade-off:* one extra round trip per checkout. That is a fraction of a
millisecond against a service whose unit of work is an LLM call over a diff.

### D4 — The truncate list comes from the metadata

`Base.metadata.sorted_tables` gives the table names in dependency order;
`TRUNCATE` with `CASCADE` does not need the order, so a sorted list of names is
enough.

*Why:* the current literal repeats what the metadata already knows, and the
failure mode of forgetting an entry is a test seeing another test's rows, which
looks like flakiness rather than a missing name.

## Risks / Trade-offs

- **The documentation tests parse markdown** → they break on reformatting.
  Mitigated by failing with the file, the line and the expected shape named,
  and by covering only two facts rather than trying to police the documents.

- **`pool_pre_ping` hides a genuinely unreachable database one round trip
  longer** → the connection still fails, just at the ping rather than the
  query, and the error names the same cause.

- **Removing the counts loses a detail someone may have relied on** → the
  README carries them, and it is the file the counts are checked against.
