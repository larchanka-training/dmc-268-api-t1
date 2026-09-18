## Why

Code review of `feat/backend-base-setup` (built on top of `feat/backend-architecture-plan`, not yet merged) found that `SqlAlchemyReviewRunRepo.update` only validates a status change against `next_status` when the incoming status differs from the row's stored status. `review-data-model` already requires that a terminal run "accepts nothing further" (`Scenario: Terminal state is final`), but the adapter's own equality check lets a same-status update reach a terminal row untouched, silently overwriting `tokens_used`, `duration_seconds`, `model`, `failure_reason` and `last_progress_at`. A redelivered or duplicate update for an already-`completed`/`failed`/`cancelled` run currently succeeds instead of being refused.

## What Changes

- `SqlAlchemyReviewRunRepo.update` refuses any update against a row whose stored status is already terminal, regardless of whether the incoming status matches it, instead of only checking `next_status` when the two statuses differ.
- A test covering the previously-uncovered path: updating an already-terminal run with the same status is refused and the stored row is unchanged.

Out of scope: the equality-skip stays for a non-terminal row (e.g. re-saving `analysing` with an advanced `last_progress_at`). No caller in this codebase performs that write today, and the two existing specs (`Terminal state is final`, `Progress resets the clock`) only constrain the terminal case and the advancing-state case — inventing a same-state heartbeat rule for the non-terminal case is not asked for by either.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. `review-data-model`'s "Terminal state is final" scenario already requires exactly this behavior; the adapter just does not implement it correctly. `skip_specs: true` is set for that reason.

## Impact

- `app/infrastructure/db/repositories.py` (`SqlAlchemyReviewRunRepo.update`)
- `tests/db/test_adapters.py`
- No schema change, no migration, no API change.
