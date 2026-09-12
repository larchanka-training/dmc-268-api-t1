## 1. Anchor identity

- [x] 1.1 Key `deduplicate` in `app/domain/dedup.py` on `(file_path, side, old_line, new_line, category)` (design: D3) and verify a new unit test that two old-side findings on deleted lines 7 and 42 of one file in one category both survive, while a genuine repeat of line 7 is dropped
- [x] 1.2 Update the `deduplicate` docstring so "the same file and line" reads as the full anchor, and verify no other module documents the old key (`grep -rn "file and line" app/`)

## 2. Constraints in the schema

- [x] 2.1 Widen `uq_findings_anchor` in `app/infrastructure/db/models.py` to `review_run_id, file_path, side, old_line, new_line, category` with `postgresql_nulls_not_distinct=True` (design: D2, D3) and verify `CreateTable` compiles to `UNIQUE NULLS NOT DISTINCT` under the postgresql dialect
- [x] 2.2 Add `postgresql_nulls_not_distinct=True` to `uq_published_comments_finding` and correct its comment, which currently claims more than the constraint enforced (specs: Published comments are tracked)
- [x] 2.3 Amend `alembic/versions/0001_baseline_schema.py` to match both constraints under the same revision id (design: D1) and verify `alembic upgrade head` on an empty database produces a schema that `alembic check` reports as matching the models
- [x] 2.4 Add an integration test that a second finding on the same old-side anchor raises `IntegrityError`, alongside the existing new-side case in `tests/db/test_constraints.py`
- [x] 2.5 Add an integration test that a second summary comment (`finding_id` NULL) for one run raises `IntegrityError`, and that two runs each keep their own summary (specs: both new scenarios)

## 3. Repository writes

- [x] 3.1 Stop `ReviewRunRepo.update` (`app/infrastructure/db/repositories.py`) writing `rejected_findings` from the entity, keeping the four outcome fields it is the only writer of (design: D4) and verify a test where `add_validated` rejects a finding, then `update` advances the status, and the counter still reads 1
- [x] 3.2 Carry those five fields through `ReviewRunRepo.add` and verify a test that adding a run built with `rejected_findings=3` reads back as 3
- [x] 3.3 Copy `source_branch` and `target_branch` in `MergeRequestRepo.update` and verify a test that retargeting a change request reaches storage
- [x] 3.4 Give `find_by_digest` a deterministic `order_by` on `id` and verify a test that two payloads sharing a digest return the same one across repeated calls

## 4. Contracts and purity

- [x] 4.1 Declare `add_validated` on the `FindingRepo` protocol in `app/application/ports/repositories.py` (design: D6) and verify `lint-imports` and a type check still pass with the concrete adapter assigned to the protocol
- [x] 4.2 Copy the JSONB body in `context_payload_to_domain` (`app/infrastructure/db/mappers.py`) and verify a test that mutating `payload.body` leaves the ORM row untouched
- [x] 4.3 Replace the `assert` in `Result.unwrap` with an explicit `ValueError` raise and verify a test that `unwrap` on a success carrying `None` returns `None` rather than raising

## 5. Environment safety

- [x] 5.1 Point the destructive fixtures in `tests/conftest.py` at `TEST_DATABASE_URL`, with no fallback to `DATABASE_URL` (design: D5), and verify integration tests skip when it is unset and run when it is set
- [x] 5.2 Document `TEST_DATABASE_URL` in `README.md`, replacing `DATABASE_URL` in the test instructions, and verify the documented commands work when followed literally. Wiring it into CI is deferred to `backend-ci-on-uv`: no workflow exists on this branch, `.github/` arrives with PR #2 (design: D5)
- [x] 5.3 Escape the DSN before it enters `configparser` in `alembic/env.py` (design: D7) and verify a test that a URL containing `p%40ss` no longer raises `ValueError: invalid interpolation syntax`, and an integration test that migrations run against such a DSN

## 6. Dependencies and closeout

- [x] 6.1 Correct the comment on the `grimp<3.16` and `import-linter<2.15` caps in `pyproject.toml` to name the real constraint (design: D8), and verify `uv sync --all-extras` installs from wheels while `uv lock --upgrade-package grimp` is shown to fall back to a source build on macOS arm64
- [x] 6.2 Note in `README.md` that a database created from the earlier baseline must be rebuilt (design: D1)
- [x] 6.3 Run `uv run ruff check .`, `uv run lint-imports` and `uv run pytest` with `TEST_DATABASE_URL` set, and verify the whole suite passes with no integration test skipped

## 7. Entrypoint

- [x] 7.1 Move `main.py` to `app/main.py` and update the `create_app` docstring in `app/api/factory.py`, which still says the root module is where the entrypoint lives (design: D9)
- [x] 7.2 Point `README.md` and `docs/BACKEND_ARCHITECTURE.md` at `uvicorn app.main:app`, and verify the documented command starts the service
- [x] 7.3 Name `app.main` as the top layer in the layers contract and add a `forbidden` contract stopping it importing `app.domain`, `app.application` or `app.infrastructure` directly, with `allow_indirect_imports` so the factory path stays legal (design: D9)
- [x] 7.4 Verify the contracts now cover the entrypoint: an import of `app.infrastructure` from `app/main.py` must make `lint-imports` report a broken contract, where the same import at the root did not
