# Backend architecture

The backend of an automated code-review agent: it watches change requests,
assembles the context around a diff, asks a model to review it, and publishes
the result back to the host.

This document describes the structure the service is built into, the rule that
keeps it from eroding, and the seams that are designed but deliberately not
built yet. It is meant to be edited in the same change that alters what it
describes.

## Layers

Four packages under `app/`, and dependencies point inward only.

```
app/api/             FastAPI routers, request and response schemas, dependencies
app/infrastructure/  adapters, SQLAlchemy models, the composition root
app/application/     use cases and the ports they depend on
app/domain/          entities, enums, and the rules, as pure functions
```

| Layer | May import | Holds |
|---|---|---|
| `domain` | nothing outward, no third-party package | entities, enums, pure decision functions |
| `application` | `domain` | use cases, port definitions |
| `infrastructure` | `application`, `domain`, `api` | adapters, models, container |
| `api` | `application`, `domain`, `infrastructure` | transport |

`api` and `infrastructure` sit at the same level and may import each other:
the composition root lives in `infrastructure` and the app factory in `api`
has to build it. What matters is that neither is reachable from `application`
or `domain`.

### The rule is enforced, not agreed

`import-linter` runs in CI with three contracts in `pyproject.toml`. The first
orders the layers. The second keeps the entrypoint thin: `app.main` may not
reach `domain`, `application` or `infrastructure` directly, only through
`app.api`. The third bars `sqlalchemy`, `fastapi`, `httpx`, `ollama`, `pika`,
`alembic` and `psycopg` from `domain` and `application` outright.

```
$ lint-imports
Dependencies point inward KEPT
The entrypoint stays thin KEPT
Inner layers know no vendor KEPT
```

A violation names the module and the line:

```
app.domain is not allowed to import sqlalchemy:
-   app.domain._violation -> sqlalchemy (l.1)
```

This is deliberate. A layering that depends on reviewers noticing decays; one
that fails the build does not.

## The pure core

Every decision is a function that takes data and returns data. No I/O, no
clock read, no randomness inside: time and identifiers arrive as arguments, so
a test asserts an exact timestamp rather than a range.

| Function | Module | Decides |
|---|---|---|
| `next_status(current, requested)` | `domain/lifecycle.py` | whether a run may change state |
| `validate_anchor(anchor, hunks)` | `domain/diff.py` | whether a finding points inside the diff |
| `deduplicate(findings)` | `domain/dedup.py` | which findings repeat one another |
| `find_stale(runs, now, limit)` | `domain/staleness.py` | which runs stopped making progress |

This is what makes a unit level possible at all. Most of the suite runs under
`pytest` with no database, no broker and no network. The tests that do need
PostgreSQL are marked `integration` and skip unless `TEST_DATABASE_URL` is
set; the README carries the current counts and the reason that variable is not
`DATABASE_URL`.

Adapters are translation and nothing else. Where an adapter appears to apply a
rule, it is calling one of the functions above:
`SqlAlchemyReviewRunRepo.update` consults `next_status` before writing a
status, and `SqlAlchemyFindingRepo.add_validated` consults `validate_anchor`
and counts the rejection on the run so a filtered finding leaves a trace.

## Ports

An external system is reached through a port owned by an inner layer, with the
adapter supplied by `infrastructure`. Ports are `typing.Protocol`, so an
adapter never imports the port and the infrastructure layer keeps no
compile-time dependency on the application layer.

### Built

| Port | Adapter | Purpose |
|---|---|---|
| `RepositoryRepo` | `SqlAlchemyRepositoryRepo` | repositories under review |
| `MergeRequestRepo` | `SqlAlchemyMergeRequestRepo` | change requests |
| `ReviewRunRepo` | `SqlAlchemyReviewRunRepo` | review runs and their lifecycle |
| `ContextPayloadRepo` | `SqlAlchemyContextPayloadRepo` | what the model was shown |
| `FindingRepo` | `SqlAlchemyFindingRepo` | findings, anchored to the diff |
| `PublishedCommentRepo` | `SqlAlchemyPublishedCommentRepo` | comments posted back |
| `UnitOfWork` | `SqlAlchemyUnitOfWork` | transaction boundary |

`app/infrastructure/container.py` is the single place a port is bound. A test
asserts that every declared port has an adapter, and that no module under
`api`, `application` or `domain` names a concrete one.

### Designed, not built

No port is written before something calls it. An interface written a ticket
ahead of its first adapter is a guess about a signature, and the guess is
found wrong exactly when the adapter arrives, at which point the interface,
its fake and its tests are all rewritten. These are recorded here instead,
which costs nothing and cannot be wrong.

| Seam | First adapter | What introducing it needs |
|---|---|---|
| `VcsGateway` | GitHub REST | fetch diff and metadata, publish comments, set status. Provider-specific payloads, comment syntax and auth stay inside the adapter. |
| `LlmGateway` | Ollama | run an analysis prompt, return structured findings. Prompt assembly is a pure function; the adapter only transports. |
| `JobQueue` | RabbitMQ | dispatch a run for async processing. One `enqueue` method; exchange layout is System Design's call. |
| `IdempotencyStore` | PostgreSQL | claim a key with `INSERT ... ON CONFLICT DO NOTHING`, replay the winner's outcome. Lands with the first endpoint that accepts a retry. |
| `RateLimiter` | PostgreSQL | fixed-window counter per subject, `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`. |
| `CacheStore` | in-process TTL dict | best-effort key/value. A miss is never an error. |

The `accounts`, `idempotency_records` and `rate_limit_counters` tables are
designed alongside those ports and arrive with them.

## Process shape

One codebase, one image, two entry points.

```
uvicorn app.main:app        HTTP: request-response, milliseconds
python -m app.worker      review consumer: minutes, bound by model latency
```

They share the domain, the repositories and the database, and communicate
through the queue rather than over HTTP. This is a modular monolith, not a set
of services.

Microservices solve an organisational problem: letting independent teams
release without coordinating. This is one team, so the cost arrives without
the benefit. Network calls where function calls would do, partial failure,
contract versioning, tracing across process boundaries, and a local
environment that needs several containers before anything runs. Idempotency is
the clearest example: one concern in one place here, and one that would have
to be solved again at every boundary we invented.

The asymmetry between the two profiles is real, but two processes cover it,
because they never call each other synchronously.

**When to actually split.** When a component needs a different runtime. The
visible candidate is AST parsing for the fourth context tier: if the
tree-sitter Python bindings disappoint, a parser service is the first thing
that should leave. Extracting it means giving one port a network adapter, not
restructuring the code, which is the Strangler Fig pattern and the reason a
modular monolith is the usual starting point for it.

Deployment topology belongs to System Design. This records what the backend
assumes.

## Data

See [the data model](erd.md) for the diagram and the constraints.

`ReviewRun` is the ticket's `ReviewJob`. The name changed because "job" is
also going to be the RabbitMQ message, and a durable row sharing a name with a
transient message is how people end up debugging the wrong thing.

Identifiers are UUIDv7 generated in the domain, never by a database default.
Time-ordered keys land at the right edge of the index instead of scattering
across it the way `uuid4` does, and an entity is complete in memory before
anything touches PostgreSQL. `uuid.uuid7()` is standard library from 3.14.

Timestamps are declared `DateTime(timezone=True)` explicitly. A bare
`Mapped[datetime]` compiles to `TIMESTAMP WITHOUT TIME ZONE`, which reads
correctly and is wrong: a run that started before a daylight-saving shift
would appear to finish before it began. The schema tests assert against
compiled DDL rather than the annotation, because the annotation is exactly
what looks right while being wrong.

## Migrations

Schema changes reach a database only through a reviewed Alembic revision. The
application creates nothing at startup.

- Filenames carry a zero-padded prefix: `0001_baseline_schema.py`.
- Exactly one head. Two branches that each add a migration rebase into a line.
- Every migration is reversible. `upgrade head`, `downgrade base`, `upgrade
  head` again must all succeed and leave nothing behind.
- Generated migrations are reviewed before they are committed. The baseline
  needed it: autogenerate dropped the tables on downgrade but left all seven
  native enum types behind, so the reversal was incomplete and the next
  upgrade would have failed creating types that already existed.
- `env.py` reads `DATABASE_URL` through `Settings`, so `alembic.ini` carries no
  connection string and the two cannot disagree. A caller that has already set
  the option keeps it, which is how the test suite points migrations at
  `TEST_DATABASE_URL` without touching what the service reads. The value is
  escaped on the way in, because `configparser` treats `%` as interpolation and
  rejects a percent-encoded password outright.

An integration test migrates a disposable database to head and asserts
autogenerate finds nothing to do, so models and schema cannot drift apart
silently.

**Adding a value to an enum** needs `ALTER TYPE ... ADD VALUE`, which cannot
run inside a transaction on older PostgreSQL. Put it in its own migration:

```python
def upgrade() -> None:
    op.execute("COMMIT")
    op.execute("ALTER TYPE finding_category ADD VALUE 'maintainability'")
```

Removing one means recreating the type, which is why the sets are small and
change rarely.

## Configuration

Everything comes from one validated object assembled at startup. Below the
composition root, layers receive what they need as arguments; only
`app/config.py` reads the environment.

Startup fails loudly when a required setting is missing, naming it. There is
one setting today, `DATABASE_URL`, because nothing reads more than that yet.
Auth, rate-limit and cache settings arrive with the code that uses them.

## Observability

What a run costs is a column, not a metrics stack. `review_runs` stores
`model`, `tokens_used`, `duration_seconds` and `failure_reason` on the row.

The questions this service will be asked are why a review took four minutes,
what last week cost, and which runs fail and where. All three are answerable
with SQL over a table that has to exist anyway, and the answers stay attached
to the run someone is looking at.

Prometheus, Grafana and distributed tracing exist to reconstruct a request
that crossed many services. Two processes sharing a database do not have that
problem. Structured logs carrying a run id are the next step if that changes.

## Extending

**Adding GitLab.** A `VcsGateway` adapter and one more value in the `provider`
enum. No migration: every record that mirrors a host object already carries a
provider discriminator next to the host's own id, and no use case names a
provider.

**Adding Redis.** One adapter class per port and a binding in the container.
No call site changes, because callers will have been written against
`IdempotencyStore`, `RateLimiter` and `CacheStore` from the start. The trigger
is the in-process cache: it is correct for one process and useless across
replicas, so the moment a second replica exists, it is wrong.

**Changing the database.** Harder, and accepted. The schema uses native enums,
JSONB, arrays, a partial unique index and `ON CONFLICT`, none of which port to
another engine. That is a deliberate trade: using the database as a database
rather than as a row store. The partial index in particular is what makes "one
active run per commit" atomic instead of a race between `SELECT` and `INSERT`.

## Threat model

A code-review agent reads attacker-influenced input by design, holds
credentials that can write to repositories, and stores other people's source
code.

**Prompt injection through the reviewed code.** The diff is attacker
controlled. A pull request can contain `# IGNORE ALL PREVIOUS INSTRUCTIONS` in
a comment, a docstring or a test fixture. The defences are structural, because
filtering for phrases loses to paraphrase: the diff is passed as delimited
data and never concatenated into the instruction, the model returns structured
output rather than prose that gets parsed, model output never selects an
action, and the review model is given no tools. `validate_anchor` is already
one such defence, since a finding invented about a file outside the diff
cannot be stored.

**Secrets in `context_payloads`.** That table holds source code verbatim. A
developer who commits an API key gets it copied into our database and kept.
Redaction belongs before the insert, and `ContextPayloadRepo` is the choke
point where it can be enforced for every writer. Retention is a
data-protection question, not only a storage one.

**Webhook authenticity.** GitHub signs deliveries with an HMAC over the body.
Without verifying it, anyone who learns the endpoint can make the service
review anything and spend model budget doing it. Lands with the endpoint.

**Credential scope.** Publishing comments needs write access. A GitHub App
with per-installation tokens limits a leak to one installation; a personal
access token would expose every repository its owner can reach.

**Registration authorisation.** Registering a repository must verify the
caller has access to it, or someone can subscribe a repository they do not own
and read the analysis of it.

**Suggestions are a write path.** A finding can carry a one-click-apply patch,
which is a model-authored change proposed into someone's branch. It stays a
suggestion a human accepts. Nothing auto-applies.

**Resource exhaustion.** A change request can be arbitrarily large. Hard
limits on files, diff bytes and run duration belong with the context builder.

**Self-hosting the model is a security property.** Ollama running in our own
network removes the largest exfiltration path. Moving to a hosted API later
would be a security decision, not only an operational one.

Of these, this document's own change owns the first two, because they shape
`ContextPayload.body` and identify the sensitive table. The rest are recorded
so the tickets that add endpoints and the pipeline start from a list rather
than from memory.

## How the code is expected to look

- **Pure core, effectful edge.** Decisions are functions over data. Time,
  identifiers and randomness are arguments, never ambient.
- **Thin adapters.** Translation between an external shape and a domain shape.
  A branch expressing a business rule is in the wrong layer.
- **No port without a caller.** Where the deliverable is the seam itself,
  describe it here rather than committing unused code.
- **Single-source what would drift.** Enums, the transition table and the
  timestamp mixin exist once. Six similar-looking repository methods are
  coincidence, not shared meaning, and get no base class.
- **Prefer the boring construct.** Fixed-window rate limiting over sliding. A
  dictionary over a cache library. Where the simple version has a ceiling,
  document the ceiling and the upgrade path instead of pre-building it.
- **Testability is the acceptance criterion.** If a rule can only be tested by
  standing up infrastructure, it is in the wrong layer.
