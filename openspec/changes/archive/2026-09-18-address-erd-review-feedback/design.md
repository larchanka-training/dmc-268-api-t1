## Context

See proposal.md for why. Current state that shapes the approach:

- Six foreign keys in `models.py` and `0001_baseline_schema.py` declare `ondelete="CASCADE"`. Nothing in `app/` deletes a row. The only caller of deletion is `tests/db/test_constraints.py::test_deleting_a_repository_takes_its_merge_requests`.
- `RepositoryRow.merge_requests`, `MergeRequestRow.repository`, `MergeRequestRow.review_runs` and `ReviewRunRow.merge_request` are ORM relationships that no code reads (the adapters query by foreign key). Two of them carry `cascade="all, delete-orphan", passive_deletes=True`.
- Every other constrained column is a native PostgreSQL enum built from `app/domain/enums.py` by `_enum()`. `merge_requests.state` is the only exception (`String(32)`, `state: str` on the entity).
- #5 is unmerged and no environment runs this schema, so `0001` can still be edited instead of adding `0002`.

## Goals / Non-Goals

**Goals:**
- Make the database refuse a deletion that would destroy dependants, regardless of which code path attempts it.
- Bring `state` in line with the other constrained columns.
- Make `docs/erd.md` answer "what is this table for" and "what does this field mean" without reading the code.

**Non-Goals:**
- Soft delete, `deleted_at`, or any repository disconnect/purge flow.
- Translating GitHub/GitLab states into the new enum. No provider adapter exists yet.
- Renaming tables.

## Decisions

### RESTRICT, not CASCADE and not soft delete

`ON DELETE RESTRICT` on all six foreign keys: `merge_requests.repository_id`, `review_runs.merge_request_id`, `context_payloads.review_run_id`, `findings.review_run_id`, `published_comments.review_run_id`, `published_comments.finding_id`.

- CASCADE was the rule for a deletion that nothing performs. What it would destroy is exactly the data that has to outlive a row: `provider_comment_id` (without it the system cannot edit or delete a comment it posted) and `tokens_used`/`duration_seconds`.
- Soft delete (the reviewer's first suggestion) answers a question nobody has asked yet, and it costs a `deleted_at IS NULL` filter in every query and in the partial unique index on `review_runs`. It is also wrong for `context_payloads`: that table holds third-party source code, and when a repository is disconnected the data-protection answer is a real delete, not a hidden row. The disconnect flow will choose per table what is deleted and what is kept. It will be written together with its caller.
- RESTRICT over NO ACTION: both refuse. RESTRICT checks immediately and cannot be deferred, so a refusal happens at the statement that caused it and never at commit time. No deferred constraints are planned.

### Keep the relationships, drop their delete cascade

Once the foreign key is RESTRICT, `cascade="all, delete-orphan"` would still make the ORM delete loaded children itself before deleting the parent, bypassing the rule the database is meant to enforce. The first plan was to delete the four relationships, since no code reads them. Implementation showed they do have a caller: the unit of work orders INSERTs by relationship, not by foreign key. Without them, a repository and its merge request flushed in one transaction are inserted child-first, and nine adapter tests fail on a foreign-key violation.

So they stay, with the delete cascade removed and `passive_deletes="all"`. With that setting the ORM neither deletes children nor nulls their foreign key when a parent is deleted. The DELETE goes to the database, and RESTRICT refuses it. A comment on the model says why attributes nobody reads exist, and a test deletes a parent through the ORM with its children loaded.

### `merge_request_state` enum: `open`, `closed`, `merged`

- Added as `MergeRequestState(StrEnum)` in `app/domain/enums.py` and mapped through `_enum()`. Like the other enums, it is defined in one place.
- Three values, not the provider sets. GitLab also has `locked`, and GitHub has no `merged` state (it is `closed` plus `merged_at`). Both translate into these three. The translation belongs in the provider adapter when it is written, and the spec requires states to be translated before they are stored.
- Drafts are not a state. On both hosts a draft is a flag on an open request.

### `merge_requests.head_sha` stays

The reviewer asked whether the table is needed at all when one change request has many commits. It is needed, because it holds what every run shares: number, title, branches, author, and the `(repository_id, number)` identity. The confusion comes from `head_sha` appearing in two tables with no explanation. The column stays, because it is the value a new run is compared against to see whether a completed run has become outdated. The ERD and the spec now define both meanings. No schema change.

### `review_runs` is not renamed

The reviewer suggested a queue-related name. The row lives far longer than its time in the queue: it carries status through analysis and publication, the failure reason, the cost figures, and it owns the findings and published comments. `queued` is one of seven states. The queue itself is RabbitMQ, and its message is the `ReviewJob`. A queue-flavoured table name would recreate the durable-row/transient-message ambiguity that `erd.md` lines 7–9 explain avoiding. Recorded here and in the reply on the PR. The name stays open to a concrete alternative that does not point at the queue.

### Edit `0001` in place

A second migration would record a history nobody lived through. The baseline is still in review, and `test_drift.py` will catch the models and the migration drifting apart. The type list `ENUM_TYPES` gets `merge_request_state`, so downgrade still leaves no orphaned types. The "seven" in `BACKEND_ARCHITECTURE.md` becomes "eight".

## Risks / Trade-offs

- [A local database built from the earlier `0001` carries CASCADE, and the revision id is unchanged, so `upgrade head` reports nothing to do] → The PR description and the reply tell anyone with a local DB to run `alembic downgrade base && alembic upgrade head`. CI builds from scratch.
- [RESTRICT makes future cleanup code more verbose, because it has to delete children in order] → That is intended: the order and the choice of what survives become visible in code instead of implied by DDL.
- [PR #9 carries a copy of `models.py`/`entities.py` from #5 with its own edits] → Small overlapping hunks (`state` column, relationship lines). Whichever PR merges second rebases. Flag this on #9.
- [`TRUNCATE ... CASCADE` in `tests/conftest.py`] → Unaffected: TRUNCATE's CASCADE is independent of the foreign key's delete rule.
