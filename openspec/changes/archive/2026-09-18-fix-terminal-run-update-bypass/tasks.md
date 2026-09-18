## 1. Adapter fix

- [x] 1.1 In `SqlAlchemyReviewRunRepo.update` (`app/infrastructure/db/repositories.py`), route a terminal stored status through `next_status` even when the status is unchanged (`if row.status in TERMINAL_STATUSES or row.status != run.status`), so the refusal and its message stay owned by the domain (design: D1); verify `TERMINAL_STATUSES` is already imported and `uv run ruff check .` is clean (mypy is not on this branch)

## 2. Test coverage

- [x] 2.1 Add a test to `tests/db/test_adapters.py` asserting that updating an already-`completed` run with the *same* status (`ReviewRunStatus.COMPLETED`) raises `ValueError` and leaves `tokens_used`/`duration_seconds`/`model`/`failure_reason` on the stored row unchanged; verify it fails against the current code before the fix and passes after
- [x] 2.2 Confirm `test_the_adapter_refuses_an_illegal_transition` (genuine transition, e.g. `queued` → `completed`) still passes unchanged

## 3. Close out

- [x] 3.1 Run `uv run ruff check .`, `uv run lint-imports`, and `TEST_DATABASE_URL=... uv run pytest tests/db/test_adapters.py` with no skips; verify all pass
