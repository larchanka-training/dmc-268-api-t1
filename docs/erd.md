# Data model

Source of truth for the shape of the database. The tables are created by
`alembic/versions/0001_baseline_schema.py`; a test asserts this diagram's
tables and the models do not drift apart.

`ReviewRun` is the ticket's `ReviewJob`. The name changed because "job" is
also going to be the RabbitMQ message, and a durable row sharing a name with a
transient message is how people end up debugging the wrong thing.

```mermaid
erDiagram
    repositories ||--o{ merge_requests : "has"
    merge_requests ||--o{ review_runs : "reviewed by"
    review_runs ||--o{ context_payloads : "was shown"
    review_runs ||--o{ findings : "produced"
    review_runs ||--o{ published_comments : "posted"
    findings ||--o| published_comments : "became"

    repositories {
        provider provider "github or gitlab"
        text provider_id "id on the host"
        text full_name
        text default_branch
        bool auto_review_enabled "review on every push"
        uuid id PK
        timestamptz created_at
        timestamptz updated_at
    }
    merge_requests {
        uuid repository_id FK
        int number "PR or MR number"
        text title
        text description
        text author
        text source_branch
        text target_branch
        text head_sha "commit under review"
        text state
        uuid id PK
        timestamptz created_at
        timestamptz updated_at
    }
    review_runs {
        uuid merge_request_id FK
        text head_sha
        review_run_status status "queued to completed"
        trigger_source trigger "webhook, manual, mention"
        timestamptz last_progress_at "drives the stale sweep"
        text failure_reason
        text model
        bigint tokens_used
        float duration_seconds
        int rejected_findings "anchors outside the diff"
        uuid id PK
        timestamptz created_at
        timestamptz updated_at
    }
    context_payloads {
        uuid review_run_id FK
        int chunk_index
        text_array tiers "diff, surrounding, whole_file, ast"
        text_array file_paths
        int token_count
        text content_sha256 "reuse key"
        jsonb body "what the model was shown"
        uuid id PK
        timestamptz created_at
        timestamptz updated_at
    }
    findings {
        uuid review_run_id FK
        text file_path
        diff_side side
        int old_line
        int new_line "must be inside the diff"
        finding_category category "security, correctness, performance, readability"
        finding_severity severity
        text message
        text suggestion
        float confidence
        uuid id PK
        timestamptz created_at
        timestamptz updated_at
    }
    published_comments {
        uuid review_run_id FK
        uuid finding_id FK "null on a summary"
        text provider_comment_id
        comment_kind kind "summary or inline"
        timestamptz published_at
        uuid id PK
        timestamptz created_at
        timestamptz updated_at
    }
```

## Rules the schema enforces

These are constraints, not conventions, so no code path can forget them.

| Rule | How |
|---|---|
| A repository is one row per host | `UNIQUE (provider, provider_id)`. The same `full_name` on two hosts is two rows. |
| A change request number is unique in its repository | `UNIQUE (repository_id, number)` |
| At most one unfinished run per commit | Partial `UNIQUE (merge_request_id, head_sha) WHERE status NOT IN ('cancelled','completed','failed')`. A finished run does not block a re-review. |
| Context chunks keep their order | `UNIQUE (review_run_id, chunk_index)` |
| A finding cannot repeat in a run | `UNIQUE (review_run_id, file_path, new_line, category)` |
| A finding is published once per run | `UNIQUE (review_run_id, finding_id)` |
| Deleting a repository removes everything under it | `ON DELETE CASCADE` down the chain |

## Two things worth knowing

**The partial index needs a reaper.** Allowing one unfinished run per commit is
what stops a redelivered webhook starting a second review. It also means a
worker that dies mid-run leaves the run non-terminal and blocks that commit
forever. `last_progress_at` and `find_stale` exist for that; the sweep that
uses them lands with the worker.

**`context_payloads` holds other people's source code.** It is the sensitive
table. Secret redaction belongs before the insert, and retention is a
data-protection question as much as a storage one.
