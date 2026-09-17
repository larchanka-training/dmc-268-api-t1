## 1. Change request state as a domain enum

- [x] 1.1 Add `MergeRequestState(StrEnum)` with `open`, `closed`, `merged` to `app/domain/enums.py`; change `MergeRequest.state` in `app/domain/entities.py` to `MergeRequestState`; verify `uv run pytest tests/domain` passes (mypy is not on this branch; it arrives with #9)
- [x] 1.2 Map `MergeRequestRow.state` through `_enum(MergeRequestState, "merge_request_state")` in `app/infrastructure/db/models.py` and make the `merge_requests` mapper and repository adapter (`app/infrastructure/db/mappers.py`, `app/infrastructure/db/repositories.py`) carry the enum; verify `uv run ruff check .` and the adapter tests pass
- [x] 1.3 Replace `state="open"` in the fixtures in `tests/db/test_adapters.py` and `tests/db/test_constraints.py` (`make_mr`) with `MergeRequestState.OPEN`; add `test_a_merge_request_state_outside_the_enum_is_refused` to `tests/db/test_constraints.py` (raw `INSERT` with `'draft'` raises); verify with `TEST_DATABASE_URL=... uv run pytest tests/db`

## 2. RESTRICT instead of CASCADE

- [x] 2.1 In `app/infrastructure/db/models.py`, change all six `ForeignKey(..., ondelete="CASCADE")` to `ondelete="RESTRICT"` and replace `cascade="all, delete-orphan", passive_deletes=True` on `RepositoryRow.merge_requests` and `MergeRequestRow.review_runs` with `passive_deletes="all"` (the relationships stay because the unit of work orders INSERTs by them); verify `grep -rn CASCADE app` returns nothing, `uv run lint-imports` keeps 3 contracts, and an ORM `session.delete` of a parent with loaded children raises `RestrictViolation`
- [x] 2.2 Replace `test_children_cascade_from_their_parent` in `tests/db/test_schema.py` with `test_children_restrict_deletion_of_their_parent`, covering all five child tables and asserting `ON DELETE RESTRICT` and no `CASCADE` in the DDL; verify `uv run pytest tests/db/test_schema.py`
- [x] 2.3 In `tests/db/test_constraints.py`, replace `test_deleting_a_repository_takes_its_merge_requests` with refusal tests for each spec scenario: a repository with a merge request, a merge request with a run, a run with a finding, a published finding (the comment's `provider_comment_id` survives), and one test that a leaf row without dependants deletes, plus deleting a parent through the ORM with its children loaded; verify with `TEST_DATABASE_URL=... uv run pytest tests/db/test_constraints.py`

## 3. Baseline migration

- [x] 3.1 Edit `alembic/versions/0001_baseline_schema.py`: add the `merge_request_state` enum column, switch all six `ForeignKeyConstraint(..., ondelete='CASCADE')` to `'RESTRICT'`, and add `merge_request_state` to `ENUM_TYPES`; verify `alembic upgrade head`, `downgrade base` (0 tables, 0 types), then `upgrade head` again on a fresh database, and that `tests/db/test_drift.py` passes

## 4. Documentation

- [x] 4.1 In `docs/erd.md`, change `merge_requests.state` to `merge_request_state state "open, closed, merged"` and add inline descriptions for `merge_requests.head_sha` ("latest known head"), `review_runs.head_sha` ("commit this run reviewed"), `tiers`, `content_sha256`, `confidence`, `provider_comment_id`, and a sharper one for `rejected_findings`; verify the `mermaid` block renders on GitHub (preview on the PR)
- [x] 4.2 Add a "Tables" section to `docs/erd.md` with one or two sentences per table on what it is for (including why `context_payloads` stores exactly what the model was shown and why `findings` is separate from `published_comments`), plus a short note on why the table is `review_runs` and not a queue name; verify `uv run pytest tests/test_docs.py` passes
- [x] 4.3 Replace the rule "Deleting a repository removes everything under it" in `docs/erd.md` with "Deleting a row never removes its dependants — `ON DELETE RESTRICT` on every foreign key", with one line on `provider_comment_id`; change "seven" to "eight" enum types in `docs/BACKEND_ARCHITECTURE.md`; verify `grep -rn "CASCADE\|seven" docs` shows only intended mentions

## 5. Verification and review follow-up

- [x] 5.1 Run the full check on a clean database: `uv run ruff check .`, `uv run lint-imports`, `uv run pytest` with and without `TEST_DATABASE_URL`; update the test counts in `README.md` to match the run
- [x] 5.2 After pushing, reply to each of DrZeD-13's five comments on `docs/erd.md` in PR #5 with what changed (or, for the rename, why not), note the local DB recreate step in the PR, and flag the overlapping `models.py`/`entities.py` hunks on PR #9
