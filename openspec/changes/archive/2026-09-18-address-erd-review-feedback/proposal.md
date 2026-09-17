## Why

The second review of PR #5 (DrZeD-13, on `docs/erd.md`) found real gaps in the data model and its document. Deleting a repository silently erases everything under it, including the provider's id of every comment already posted to GitHub, so a posted comment can no longer be updated or removed. The run metrics go with it. `merge_requests.state` is free text with no defined values. The ERD lists columns but never says what each table is for or what the less obvious fields mean. #5 is not merged yet, so the baseline migration can still be corrected in place and no second migration is needed.

## What Changes

- **BREAKING** (schema, unreleased): every foreign key in the review chain changes from `ON DELETE CASCADE` to `ON DELETE RESTRICT`. Deleting a row that still has dependants is refused. It no longer removes them.
- The unused ORM relationships that carried `cascade="all, delete-orphan"` are removed.
- `merge_requests.state` becomes a native enum `merge_request_state` with values `open`, `closed`, `merged`, defined once in the domain.
- `alembic/versions/0001_baseline_schema.py` is edited in place: RESTRICT foreign keys, the new enum column, and the new type added to the list the downgrade drops.
- `docs/erd.md` gains a "Tables" section that gives the purpose of each table, descriptions for the non-obvious fields (`head_sha` on both tables, `state`, `rejected_findings`, `tiers`, `content_sha256`, `confidence`, `provider_comment_id`), and a corrected deletion rule.
- Not changed: soft delete is not introduced, `merge_requests` is kept, and `review_runs` is not renamed. The reasoning is in design.md.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `review-data-model`: change-request state becomes a closed set; the head commit on a change request is defined as the latest known head, distinct from the commit a run reviewed; deleting a repository no longer removes its history; stored review history is protected from implicit deletion.

## Impact

- Code: `app/domain/enums.py`, `app/domain/entities.py`, `app/infrastructure/db/models.py`, `app/infrastructure/db/mappers.py`, `app/infrastructure/db/repositories.py` (state type only).
- Migration: `alembic/versions/0001_baseline_schema.py`. Local databases created from the earlier baseline must be recreated (`alembic downgrade base && alembic upgrade head`).
- Tests: `tests/db/test_schema.py` (the cascade test is inverted), `tests/db/test_constraints.py` (the deletion test is inverted, and a state enum test is added), and the fixtures in `tests/db/test_adapters.py` and `tests/db/test_constraints.py` that pass `state="open"`.
- Docs: `docs/erd.md`; `docs/BACKEND_ARCHITECTURE.md` where it counts the enum types ("seven").
- PR #9 (`feat/backend-base-setup`) copies #5's `models.py` and `entities.py` and changes both, so whichever merges second has to rebase over these edits.
