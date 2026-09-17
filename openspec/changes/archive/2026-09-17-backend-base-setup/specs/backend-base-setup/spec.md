## Purpose
Defines the developer-facing tooling contract of the backend: how new tooling is added, which static checks gate a commit and a merge, how a contributor runs the whole stack locally with one command, how Redis is provisioned ahead of having a caller, and how the service answers a liveness probe independently of its own dependencies.

## ADDED Requirements

### Requirement: New tooling is managed through uv, not out-of-band

Every development tool this project adds (a linter, a type checker, a
commit-hook runner) SHALL be declared as a `uv`-managed dependency in
`pyproject.toml` and pinned in `uv.lock`. No second dependency list (a
`requirements*.txt`, a global `pip install`, a `pipx install`) SHALL be
introduced or documented as an install path.

#### Scenario: A new tool arrives as a uv dev dependency

- **WHEN** a development tool is added to the project
- **THEN** it appears in `pyproject.toml`'s dependency list and `uv.lock`, and a fresh `uv sync --all-extras` installs it with no other command

#### Scenario: No second dependency list exists

- **WHEN** the repository is searched for an alternative install path (`requirements*.txt`, a documented global `pip install`, a documented `pipx install`)
- **THEN** none is found; `uv sync` is the only documented installation command

### Requirement: Static type checking gate

`mypy` SHALL run in strict mode over the `app` package. CI and the local
pre-commit hook SHALL both invoke it, and either SHALL fail the build when
it reports an error. A relaxation of a strict-mode rule SHALL be scoped to a
named module via `[[tool.mypy.overrides]]` and SHALL NOT apply to `app/`.

#### Scenario: The gate passes on the current tree

- **WHEN** `mypy` runs over the repository
- **THEN** it reports zero errors

#### Scenario: A strict-mode violation in `app/` fails the build

- **WHEN** a change introduces an untyped function signature, a missing generic type argument, or an incompatible assignment inside `app/`
- **THEN** `mypy` fails, naming the file and the line

#### Scenario: A test-only relaxation stays inside `tests/`

- **WHEN** a `[[tool.mypy.overrides]]` entry relaxes a strict-mode rule for `tests.*`
- **THEN** the same rule still applies at full strength to every module under `app/`

### Requirement: Pre-commit hook enforces the same checks before a commit is made

The repository SHALL provide a `pre-commit` configuration, installed and run
through `uv`, executing the project's style check (`ruff`), its type check
(`mypy`), and its layering check (`lint-imports`) before a commit is created.
The set of rules enforced at commit time SHALL NOT diverge from the set CI
enforces.

#### Scenario: A local violation is blocked before it reaches history

- **WHEN** a commit is attempted while a staged file violates `ruff`, `mypy`, or the layering rule
- **THEN** `pre-commit` blocks the commit and reports which check failed

#### Scenario: A tree that passes pre-commit also passes CI's equivalent steps

- **WHEN** `pre-commit run --all-files` passes locally
- **THEN** the same tree's `ruff check .`, `mypy .`, and `lint-imports` CI steps also pass, because both invoke the same tool versions from the same lockfile

### Requirement: Containerized local development environment

The repository SHALL provide a `docker-compose.yml` that starts the API
together with PostgreSQL and Redis using one command. The API service SHALL
wait for PostgreSQL's health check before starting, SHALL apply pending
Alembic migrations before serving traffic, and SHALL read its database
connection from compose-provided configuration rather than requiring a
developer to export it by hand.

#### Scenario: One command starts the whole stack

- **WHEN** `docker compose up` is run in a clean checkout with Docker available
- **THEN** the API, PostgreSQL, and Redis containers all start, and the API becomes reachable once PostgreSQL is healthy

#### Scenario: The API waits rather than crash-loops

- **WHEN** the API container starts before PostgreSQL has finished becoming ready
- **THEN** it waits on PostgreSQL's health check rather than failing on a connection refusal

#### Scenario: Data survives a restart

- **WHEN** the compose stack is stopped and started again without removing its volumes
- **THEN** data written to PostgreSQL during the previous run is still present

### Requirement: Redis is provisioned but not wired to application code

Redis SHALL be available to the local `docker compose` stack as a runnable
service. No Redis client dependency, port, adapter, or cache abstraction
SHALL be added to `app/` until a use case that needs one exists.

#### Scenario: Redis runs with no application caller

- **WHEN** the compose stack is running
- **THEN** the `redis` container is up, and no request path in the API depends on it responding

#### Scenario: No Redis code exists yet

- **WHEN** `app/` and `pyproject.toml` are searched for a Redis client reference
- **THEN** no import, dependency, port, or adapter is found; the only reference to Redis in the repository is the compose service definition

### Requirement: Liveness check endpoint

The existing `GET /health` endpoint SHALL serve as the liveness check the
task calls "`healthcheck`": it SHALL return HTTP 200 once the process is
running, independent of database or cache connectivity. No separate
`/healthcheck` path SHALL be added — `/health` is the one liveness contract,
unchanged from what `backend-architecture` shipped.

#### Scenario: Health endpoint answers once the process is up

- **WHEN** `GET /health` is called against a running instance
- **THEN** the response status is 200

#### Scenario: Health endpoint does not depend on the database

- **WHEN** `GET /health` is called while PostgreSQL is unreachable
- **THEN** the response status is still 200

#### Scenario: No second health path is introduced

- **WHEN** the API's routes are inspected
- **THEN** exactly one liveness path exists (`/health`), and no `/healthcheck` route is registered
