## 1. Dependencies and layer skeleton

- [x] 1.1 Add `requires-python = "==3.14.*"` to `[project]` and a `.python-version` file holding `3.14`; verify installation is refused on an older interpreter (`/usr/bin/python3 -m pip install -e .` fails naming the requirement) and that `uv run python --version` reports 3.14 in a clean checkout despite `python3` on `PATH` resolving elsewhere (design: D14)
- [x] 1.2 Add `sqlalchemy>=2.0`, `alembic`, `psycopg[binary]`, `pydantic-settings` to `pyproject.toml` runtime deps and `import-linter` to dev deps; verify `uv sync` succeeds and `python -c "import sqlalchemy, alembic"` runs clean
- [x] 1.3 Create the `app/domain`, `app/application`, `app/infrastructure`, `app/api` packages; verify `python -c "import app.domain, app.application, app.infrastructure, app.api"` succeeds
- [x] 1.4 Move app construction into `app/api/factory.py::create_app()` and reduce `main.py` to `app = create_app()`; verify `tests/test_health.py` still passes and `GET /` and `GET /health` return their current payloads byte-for-byte
- [x] 1.5 Add `app/config.py` with a `pydantic-settings` `Settings` object holding `DATABASE_URL` and nothing else — no JWT or rate-limit settings, since nothing reads them yet (YAGNI); verify startup aborts naming the missing setting when `DATABASE_URL` is unset (spec: backend-architecture — Missing required setting stops startup)
- [x] 1.6 Verify no module outside `app/config.py` reads the environment: `grep -rn "os.environ\|getenv" app/` returns matches only in that file (spec: backend-architecture — No environment access outside configuration assembly)

## 2. Dependency rule enforcement

- [x] 2.1 Add `import-linter` contracts to `pyproject.toml`: a `layers` contract ordering `app.api`/`app.infrastructure` above `app.application` above `app.domain`, and a `forbidden` contract barring `sqlalchemy`, `fastapi`, `httpx`, `ollama`, and `pika` from `app.domain` and `app.application`; verify `lint-imports` passes
- [x] 2.2 Prove the check bites: temporarily add an outward import in a domain module, verify `lint-imports` fails naming the offending module and import, then revert (spec: backend-architecture — Violation is caught mechanically)

## 3. Domain layer — pure functions, no I/O

- [x] 3.1 Define the domain enums — `Provider`, `ReviewRunStatus`, `FindingCategory`, `FindingSeverity`, `CommentKind`, `TriggerSource` — as the single source of truth in `app/domain/enums.py`; verify unit tests assert each member set matches the spec's constrained values and that no second definition exists elsewhere (`grep -rn "class .*Status" app/`)
- [x] 3.2 Define `Repository`, `MergeRequest`, `ReviewRun`, `ContextPayload`, `Finding`, `PublishedComment` as frozen dataclasses in `app/domain/`; verify they import with no third-party dependency and `lint-imports` stays green
- [x] 3.3 Write the pure `next_status(current, requested) -> Result` transition function with the legal-transition table as data, taking no clock and performing no I/O; verify unit tests cover a legal transition, a rejected illegal one, and that each of completed/failed/cancelled rejects every further transition (spec: review-data-model — Terminal state is final)
- [x] 3.4 Write the pure `validate_anchor(anchor, hunks) -> Result` function taking parsed hunk ranges as an argument; verify unit tests on literal data cover an in-diff anchor accepted, an out-of-diff line rejected, and a file absent from the diff rejected (spec: review-data-model — Finding outside the diff is rejected)
- [x] 3.5 Write the pure `deduplicate(findings) -> tuple[kept, dropped]` function collapsing findings that share file, coordinates, and category; verify unit tests cover an exact duplicate collapsed and two findings differing only by category both kept (spec: review-data-model — Duplicate findings are collapsed)
- [x] 3.6 Verify the whole domain layer is pure: `pytest tests/domain` passes with `DATABASE_URL` unset and no network, and no domain module imports `datetime.now`, `uuid4`, or `random` at call time (times and ids arrive as arguments)

## 4. Persistence ports

- [x] 4.1 Declare the six aggregate repository ports as `Protocol`s in `app/application/ports/`; verify every method signature uses only domain types and primitives, with no `Session`, engine, or SQLAlchemy type (spec: backend-architecture — Port has no infrastructure detail in its signature)
- [x] 4.2 Declare the `UnitOfWork` port exposing the transaction boundary and the six repositories; verify `lint-imports` confirms no SQLAlchemy import reaches the application layer
- [x] 4.3 Verify no port is defined without an adapter and a caller — enumerate `app/application/ports/` and confirm each entry is bound in the composition root and exercised by a test (spec: backend-architecture — Unused port is not committed)

## 5. Persistence models

- [x] 5.1 Add the SQLAlchemy declarative base with one shared mixin defining `created_at`/`updated_at` as `mapped_column(DateTime(timezone=True))` — explicitly, since a bare `Mapped[datetime]` compiles to `TIMESTAMP WITHOUT TIME ZONE` — reused by every model; verify by compiling `CreateTable` against the PostgreSQL dialect and asserting the DDL says `TIMESTAMP WITH TIME ZONE`, not merely that the Python annotation looks right (design: D16; spec: review-data-model — Timestamps are time-zone aware)
- [x] 5.2 Add the shared `id` column as `mapped_column(Uuid, primary_key=True)` with values generated in the domain by `uuid.uuid7()` and never by a database default; verify the DDL compiles to native `UUID`, that consecutive generated ids compare in creation order, and that an entity can be constructed with its id in a unit test without a database (design: D15; spec: review-data-model — Entity carries its own key)
- [x] 5.3 Map `repositories` with a unique constraint on `(provider, provider_id)`; verify an integration test rejects a duplicate pair and accepts the same full name under two different providers (spec: backend-architecture — Same object identity on two providers does not collide)
- [x] 5.4 Map `merge_requests` with a unique constraint on `(repository_id, number)` and cascade delete from `repositories`; verify an integration test deletes a repository and observes its merge requests removed
- [x] 5.5 Map `review_runs` with the status and trigger enums, timestamps, failure reason, and model/token/duration cost columns; verify an integration test round-trips a run from queued to completed with cost recorded
- [x] 5.6 Add `last_progress_at` to `review_runs`, updated by every state transition; verify a unit test on the pure transition function asserts the supplied timestamp lands on the entity, and an integration test asserts it moves when the run advances (design: D17; spec: review-data-model — Progress resets the clock)
- [x] 5.7 Write the pure `find_stale(runs, now, limit) -> list` selecting non-terminal runs past the limit; verify unit tests on literal data cover a stale run selected, a recently advanced run skipped, and a terminal run never selected (design: D17)
- [x] 5.8 Add the partial unique index enforcing at most one non-terminal run per `(merge_request_id, head_sha)`; verify an integration test inserts a competing active run and observes the refusal, then completes the first run and inserts a re-review successfully (spec: review-data-model — One run per commit unless re-review is requested)
- [x] 5.9 Map `context_payloads` with `chunk_index`, `tiers`, `file_paths`, `token_count`, `content_sha256`, JSONB `body`, a unique constraint on `(review_run_id, chunk_index)`, and an index on `content_sha256`; verify an integration test stores two ordered chunks for one run and retrieves them in order (spec: review-data-model — Oversized context is recorded as chunks)
- [x] 5.10 Map `findings` with the anchor columns, category and severity enums, message, optional suggestion, confidence, and a unique constraint on `(review_run_id, file_path, new_line, category)`; verify an integration test observes a duplicate refused at the database level
- [x] 5.11 Map `published_comments` with the provider comment id, nullable finding reference, comment kind, and a unique constraint on `(review_run_id, finding_id)`; verify integration tests store a summary comment with a null finding, and reject a second publication of the same finding in one run (spec: review-data-model — Published comments are tracked)

## 6. Persistence adapters

- [x] 6.1 Implement the SQLAlchemy `UnitOfWork` adapter owning the session and transaction boundary; verify an integration test proves a failed unit of work rolls back every write in it
- [x] 6.2 Implement the six SQLAlchemy repository adapters as thin translation between rows and domain dataclasses, with no branching business rules; verify integration tests cover create, fetch, and update per aggregate, and that a reviewer can point to the pure function behind any rule the adapter appears to apply
- [x] 6.3 Make `ReviewRunRepo` route every status write through `next_status` and `FindingRepo` refuse an anchor not validated by `validate_anchor`, incrementing the run's rejection count; verify integration tests assert an illegal transition and an out-of-diff finding are both refused at the adapter (spec: review-data-model — Finding outside the diff is rejected)

## 7. Composition root

- [x] 7.1 Add `app/infrastructure/container.py` binding each persistence port to exactly one adapter from `Settings`; verify a test asserts every declared port has exactly one binding and no caller constructs an adapter itself (spec: backend-architecture — Adapter is selected at composition time)
- [x] 7.2 Expose the container through FastAPI dependencies in `app/api/dependencies.py`; verify a test obtains a port-typed dependency without referencing a concrete adapter type

## 8. Alembic

- [x] 8.1 Initialise `alembic/` with `env.py` reading `DATABASE_URL` from `Settings` and targeting the declarative metadata; verify `alembic current` runs against a local PostgreSQL
- [x] 8.2 Autogenerate the baseline revision, then review and hand-correct it — enum creation and drop order, the partial unique index, constraint names, `server_default` values — and save as `versions/0001_baseline_schema.py`; verify it creates every table, enum, key, constraint, and index the models declare (spec: database-migrations — Generated migration is reviewed)
- [x] 8.3 Verify reversibility end to end: `alembic upgrade head`, then `alembic downgrade base` leaving no leftover table or enum type, then `upgrade head` again succeeding (spec: database-migrations — Baseline reverses cleanly)
- [x] 8.4 Verify `alembic upgrade head` on an already-current database changes nothing and exits zero, and `alembic heads` reports exactly one head (spec: database-migrations — Applying twice is harmless, Single head is enforced)
- [x] 8.5 Confirm the application creates no schema at startup: run against an empty database and verify no tables appear and the service reports that migrations have not been applied (spec: database-migrations — Application does not mutate schema)

## 9. Drift check

- [x] 9.1 Add an `@pytest.mark.integration` test that migrates a disposable database to head and asserts Alembic autogenerate yields an empty diff against the models; verify it passes on the current tree
- [x] 9.2 Verify the drift test skips cleanly when `DATABASE_URL` is unset, and prove it bites by adding an unmapped column and observing the failure name the differing object, then revert (spec: database-migrations — Drift fails the check)

## 10. Documentation

- [x] 10.1 Write `docs/erd.md` with the ERD as a ```mermaid `erDiagram` fenced block covering the six tables, their columns, relationships and cardinalities; verify GitHub renders it as a diagram on the pushed branch rather than showing source, and that no `.mmd` or committed `.svg` exists (design: D11)
- [x] 10.2 Write `docs/BACKEND_ARCHITECTURE.md` covering the four layers, the dependency rule and how `import-linter` enforces it, the review-run lifecycle, a link to `docs/erd.md`, and the `ReviewJob` → `ReviewRun` naming mapping; verify every port in `app/application/ports/` appears with its purpose and adapter (spec: backend-architecture — Document covers every declared port)
- [x] 10.3 Document the process shape in `BACKEND_ARCHITECTURE.md`: one image with an HTTP entry point and a worker entry point, why the split is two processes rather than two services, and what would justify extracting one later (design: D19)
- [x] 10.4 Document each deferred seam — `VcsGateway`, `LlmGateway`, `JobQueue`, `IdempotencyStore`, `RateLimiter`, `CacheStore` — with its purpose, the adapter intended to satisfy it first, and what introducing it would require; verify each of the six is present and none exists as code (spec: backend-architecture — Document covers each deferred seam)
- [x] 10.5 Document the engineering principles from design.md D13 — pure core with effectful edge, thin adapters, no port without a caller, single-sourced enums — so they bind the pipeline tickets, not just this one
- [x] 10.6 Add a threat-model section to `BACKEND_ARCHITECTURE.md` from design.md D21: prompt injection through the reviewed diff and the structural defences against it, `context_payloads` as the sensitive table and where redaction must happen, webhook signature verification, credential scope, registration authorisation, suggestions as a write path, and resource limits; mark which items this change owns and which belong to later tickets (design: D21)
- [x] 10.7 Document what adding Redis and adding GitLab each require, and record the migration conventions: zero-padded filename prefixes, one head, review generated migrations, and the `ALTER TYPE ... ADD VALUE` recipe (spec: database-migrations — Naming and reviewability)

## 11. Close out

- [x] 11.1 Run the full gate — `ruff check`, `pylint`, `lint-imports`, `pytest` with `DATABASE_URL` set and unset — and verify all pass in both modes
- [x] 11.2 Open the PR against `main` closing issue #4 (`Closes #4`), listing the four DoD deliverables with links and naming what was deliberately deferred under YAGNI; verify CI is green

<!-- CI work (uv install, lint-imports step, PostgreSQL service) moved to the
     backend-ci-on-uv change: it edits a workflow that arrives with PR #2 and
     belongs to the DevOps ticket's territory, not this one. -->
<!-- Agent skills (.agents/, per-tool symlinks) are the DevTools / Agentic
     Engineer ticket's deliverable, ui-t6#18, whose DoD names that folder
     explicitly. Removed from this change. -->
