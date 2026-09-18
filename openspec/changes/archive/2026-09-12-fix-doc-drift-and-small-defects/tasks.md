## 1. Documents back in step with the code

- [x] 1.1 Correct the findings row in the constraint table of `docs/erd.md` to `UNIQUE NULLS NOT DISTINCT (review_run_id, file_path, side, old_line, new_line, category)`, and the published-comment row to carry `NULLS NOT DISTINCT`; verify both match `pg_get_constraintdef` on a migrated database
- [x] 1.2 Say in `docs/erd.md` why the key carries both line numbers and the NULL rule: only the side's own number is populated, so without them the constraint never fires on the old side (specs: review-data-model, "Duplicate findings are collapsed")
- [x] 1.3 Update `docs/BACKEND_ARCHITECTURE.md` to name three contracts and show the three-line `lint-imports` output; verify the block matches the real output of `uv run lint-imports`
- [x] 1.4 Replace the test counts in `docs/BACKEND_ARCHITECTURE.md` with a statement that needs no maintenance, naming `TEST_DATABASE_URL` (design: D1); verify no count and no bare `DATABASE_URL` remains in that paragraph

## 2. Checks so the same facts cannot drift again

- [x] 2.1 Add a test comparing the constraint table in `docs/erd.md` against the DDL compiled from `models.py` for both unique constraints, columns and `NULLS NOT DISTINCT` alike (design: D2); verify it fails when a column is removed from either side
- [x] 2.2 Add a test comparing the contract count claimed in `docs/BACKEND_ARCHITECTURE.md` against `[[tool.importlinter.contracts]]` in `pyproject.toml`; verify it fails when a contract is added without touching the document
- [x] 2.3 Confirm both tests run without a database, so they stay in the suite a laptop can run

## 3. Defects

- [x] 3.1 Add `pool_pre_ping=True` to `create_engine` in `app/infrastructure/container.py` (design: D3); verify the engine reports it through `engine.pool._pre_ping`
- [x] 3.2 Move `from dataclasses import replace` to the module imports in `app/domain/lifecycle.py`; verify `ruff check` passes and the lifecycle tests still pass
- [x] 3.3 Replace the no-op ternary in `tests/test_container.py` with the assignment it performs; verify the test still fails when a port has no adapter, by adding an orphan name to `ports.__all__` temporarily. Renaming an adapter cannot be the probe: it breaks the import in `unit_of_work.py` and the module never gets collected
- [x] 3.4 Build the `TRUNCATE` list in `tests/conftest.py` from `Base.metadata` (design: D4); verify the integration suite passes and that a table added to the metadata appears without editing the fixture

## 4. Close out

- [x] 4.1 Run `uv run ruff check .`, `uv run lint-imports` and the full suite with `TEST_DATABASE_URL` set; verify everything passes with no integration test skipped
- [x] 4.2 Re-read both documents against the code they describe once more, since this change exists because that step was skipped last time
