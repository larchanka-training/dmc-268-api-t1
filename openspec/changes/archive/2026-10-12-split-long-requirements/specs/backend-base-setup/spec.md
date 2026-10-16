## MODIFIED Requirements

### Requirement: Containerized local development environment

The repository SHALL provide a `docker-compose.yml` that starts the API and
the worker together with PostgreSQL, Redis, and RabbitMQ in one command. The
API service SHALL start after PostgreSQL's health check, SHALL apply pending
Alembic migrations before serving traffic, and SHALL read its database
connection from compose-provided configuration, not a hand-exported variable.
The API and the worker SHALL both start after RabbitMQ's health check.

#### Scenario: One command starts the whole stack

- **WHEN** `docker compose up` is run in a clean checkout with Docker available
- **THEN** the API, worker, PostgreSQL, Redis, and RabbitMQ containers all start, and the API becomes reachable once PostgreSQL is healthy

#### Scenario: The API waits rather than crash-loops

- **WHEN** the API container starts before PostgreSQL has finished becoming ready
- **THEN** it waits on PostgreSQL's health check rather than failing on a connection refusal

#### Scenario: The worker waits on RabbitMQ rather than crash-looping

- **WHEN** the worker container starts before RabbitMQ has finished becoming ready
- **THEN** it waits on RabbitMQ's health check rather than failing on a connection refusal

#### Scenario: Data survives a restart

- **WHEN** the compose stack is stopped and started again without removing its volumes
- **THEN** data written to PostgreSQL during the previous run is still present
