## Context

See `proposal.md` for motivation. What shapes the fix:

- `app/domain/lifecycle.py::next_status` already rejects a terminal `current`
  before it even looks at `requested`: `if current in TERMINAL_STATUSES: return
  Result.failure(...)`. The transition table `_ALLOWED` has no self-loop for
  any status, terminal or not — no state is listed as allowed to move to
  itself.
- `SqlAlchemyReviewRunRepo.update` only calls `next_status` when
  `row.status != run.status`. That single condition is doing two jobs: it
  skips validation for a same-status write, and it is the only thing that
  currently stops a same-status write from reaching a terminal row.
- No caller in `app/` invokes `ReviewRunRepo.update` today (grepped: only
  tests do) — the use-case layer that will drive the pipeline does not exist
  yet in this PR. So there is no evidence, in either direction, of whether a
  same-status write against a *non-terminal* row (a progress heartbeat) is a
  pattern the system needs.

## Goals / Non-Goals

**Goals:**

- A row whose stored status is already terminal refuses every `update` call,
  matching `review-data-model`'s "Terminal state is final" scenario exactly:
  same status or different, the write is refused and the row is unchanged.

**Non-Goals:**

- Deciding whether a same-status update against a *non-terminal* row should be
  allowed, rejected, or given its own rule (e.g. a heartbeat that only touches
  `last_progress_at`). Nothing in `review-data-model` calls for that today, and
  no caller exists to demonstrate the need. Left exactly as it behaves now.
- Any change to `next_status`, `_ALLOWED`, or the domain lifecycle module —
  the transition table is already correct; only the adapter's guard around it
  is wrong.

## Decisions

### D1 — Guard on `row.status in TERMINAL_STATUSES`, not on equality

Replace:

```python
if row.status != run.status:
    verdict = next_status(row.status, run.status)
    if not verdict.ok:
        raise ValueError(verdict.error)
```

with a terminal check that runs unconditionally, ahead of the existing
equality-gated `next_status` call:

```python
if row.status in TERMINAL_STATUSES:
    raise ValueError(f"{row.status} is terminal; cannot move to {run.status}")
if row.status != run.status:
    verdict = next_status(row.status, run.status)
    if not verdict.ok:
        raise ValueError(verdict.error)
```

*Why:* this is the minimal change that closes exactly the gap in the proposal
— a terminal row can no longer accept a same-status write — without touching
the equality skip for a non-terminal row, which is outside this change's
scope (see Non-Goals).

*Alternative considered:* drop the `if row.status != run.status:` guard
entirely and always call `next_status`. Rejected: `_ALLOWED` has no self-loop
for any non-terminal status either, so this would also reject a same-status
update against, say, `analysing` — a behavior change with no caller to show
whether it is wanted, and not something the proposal asked for.

## Risks / Trade-offs

- **The non-terminal same-status path stays exactly as unspecified as it is
  today** → acceptable: no spec requirement and no caller exercise it, so
  leaving it alone is the YAGNI-consistent choice. If a future use case needs
  a same-status heartbeat, that is a new requirement to spec explicitly, not
  a side effect of this fix.
