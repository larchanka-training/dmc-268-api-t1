## Purpose

Defines how the database schema evolves: every structural change reaches an environment through a reviewed, ordered, reversible migration, and the schema an environment ends up with is always the one the application's models expect.

## ADDED Requirements

### Requirement: Schema changes only through migrations

Every change to the database schema SHALL be expressed as a versioned migration committed to the repository. The application SHALL NOT create, alter, or drop schema objects at startup or at runtime.

#### Scenario: Model change is accompanied by a migration

- **WHEN** a change adds, removes, or alters a persisted field, table, index, or constraint
- **THEN** the same change contains a migration expressing it

#### Scenario: Application does not mutate schema

- **WHEN** the application starts against an empty database
- **THEN** no tables are created and the application reports that migrations have not been applied

### Requirement: Baseline migration creates the full schema

The repository SHALL contain a baseline migration that takes an empty database to the complete review-pipeline schema, including all tables, primary and foreign keys, enumerated types, unique constraints, and indexes.

#### Scenario: Empty database is fully provisioned

- **WHEN** migrations are applied to an empty database
- **THEN** every table, key, enumerated type, constraint, and index the models declare is present

#### Scenario: Applying twice is harmless

- **WHEN** migrations are applied to an already up-to-date database
- **THEN** nothing changes and the command succeeds

### Requirement: Migrations are reversible

Every migration SHALL define a downgrade that reverses its upgrade. Applying a migration and then reversing it SHALL leave the schema as it was before.

#### Scenario: Baseline reverses cleanly

- **WHEN** the schema is migrated to head and then downgraded to base
- **THEN** every object the baseline created is removed and no error occurs

#### Scenario: Irreversible step is refused

- **WHEN** a migration cannot be reversed without data loss
- **THEN** it is split so that the destructive step is a separate, explicitly documented migration

### Requirement: Schema matches the models

The migration history SHALL leave the database in the state the application's declarative models describe. Any divergence SHALL be detectable automatically.

#### Scenario: No drift after migrating

- **WHEN** the schema is migrated to head and compared against the models
- **THEN** the comparison reports no difference

#### Scenario: Drift fails the check

- **WHEN** a model is changed without a corresponding migration
- **THEN** the automated drift check fails and names the differing object

### Requirement: Migration history is linear

The migration graph SHALL have exactly one head. Two changes that each add a migration SHALL be reconciled into a single ordered chain before merging.

#### Scenario: Single head is enforced

- **WHEN** the migration graph is inspected
- **THEN** exactly one head revision is reported

#### Scenario: Concurrent migrations are rebased

- **WHEN** two branches each add a migration on the same parent
- **THEN** the second to merge is rebased onto the first so the history stays linear

### Requirement: Naming and reviewability

Each migration SHALL carry a stable revision identifier, a zero-padded sequential prefix in its filename, and a description of its intent. Generated migrations SHALL be reviewed and corrected before being committed.

#### Scenario: Migration is identifiable at a glance

- **WHEN** the migrations directory is listed
- **THEN** each file shows its order and a human-readable description of what it does

#### Scenario: Generated migration is reviewed

- **WHEN** a migration is produced by autogeneration
- **THEN** it is inspected and edited for correctness before being committed, rather than committed as generated
