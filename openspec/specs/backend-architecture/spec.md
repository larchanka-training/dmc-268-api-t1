# backend-architecture Specification

## Purpose
Defines the layering contract of the code-review backend: which layers exist, which direction dependencies are allowed to point, and which capabilities are reached only through abstract ports — so that infrastructure choices (database, LLM runtime, VCS provider, cache) can be replaced without touching business logic.

## Requirements

### Requirement: Four-layer separation

The service SHALL be organised into four layers — API, application, domain, and infrastructure — where the domain layer holds entities and business invariants, the application layer holds use cases, the API layer exposes transport, and the infrastructure layer holds every adapter to an external system.

Each layer SHALL live in its own package, and no module SHALL belong to two layers.

#### Scenario: Layer packages exist and are distinct

- **WHEN** the backend source tree is inspected
- **THEN** four top-level layer packages are present, each containing only modules belonging to that layer

#### Scenario: Business rule lives in the domain layer

- **WHEN** a rule about a domain entity's validity or lifecycle is expressed in code
- **THEN** it resides in the domain layer and is reachable without constructing any transport, database, or network object

### Requirement: Inward dependency rule

Dependencies SHALL point inward only. The domain layer SHALL NOT import from the application, API, or infrastructure layers. The application layer SHALL NOT import from the API or infrastructure layers. The infrastructure and API layers MAY import from the application and domain layers.

Violations SHALL be detectable by an automated check rather than by review alone.

#### Scenario: Domain module imports nothing outward

- **WHEN** the import graph of any domain module is resolved transitively
- **THEN** it contains no module from the application, API, or infrastructure layers

#### Scenario: Application module reaches infrastructure only through a port

- **WHEN** an application use case needs an external system
- **THEN** it depends on a port declared in the application or domain layer, and never on a concrete adapter type

#### Scenario: Violation is caught mechanically

- **WHEN** a change introduces an import that points outward
- **THEN** the automated layering check fails and identifies the offending module and import

### Requirement: External systems are reached through ports

Every external system the service talks to SHALL be represented by a port — an abstract interface owned by the application or domain layer — with concrete adapters supplied by the infrastructure layer. No inner layer SHALL depend on a concrete adapter type.

A port SHALL be introduced together with the first caller that needs it. Until then the intended seam SHALL be recorded in the architecture document rather than committed as an interface with no implementation.

#### Scenario: Port has no infrastructure detail in its signature

- **WHEN** a port's methods are inspected
- **THEN** its parameter and return types are domain types or primitives, and reference no database session, HTTP client, message-broker channel, or vendor SDK type

#### Scenario: Persistence is reached through a port

- **WHEN** code outside the infrastructure layer loads or stores a domain entity
- **THEN** it does so through a repository port and a unit-of-work boundary, and never through a database session directly

#### Scenario: Adapter is selected at composition time

- **WHEN** the application is started
- **THEN** a single composition step binds each port to exactly one adapter, and no caller constructs an adapter itself

#### Scenario: Unused port is not committed

- **WHEN** the codebase is inspected for port definitions
- **THEN** every port has at least one adapter and at least one caller, and any planned-but-unbuilt seam appears only in the architecture document

### Requirement: Version-control provider is replaceable

The stored model SHALL be provider-neutral: every record that mirrors an object on a version-control host SHALL carry an explicit provider discriminator alongside that host's own identifier for the object.

Adding support for a second host SHALL require a new adapter and a new value of the provider discriminator, and SHALL NOT require adding, removing, or retyping a column.

#### Scenario: Stored records identify their provider

- **WHEN** a repository or change-request record is persisted
- **THEN** it carries an explicit provider identifier alongside the provider's own identifier for that object

#### Scenario: Same object identity on two providers does not collide

- **WHEN** two hosts assign the same identifier to different repositories
- **THEN** both records are stored and remain distinguishable by provider

#### Scenario: Second provider needs no schema change

- **WHEN** support for an additional version-control host is added later
- **THEN** the change consists of a new adapter and a new provider identifier value, with no migration that adds, removes, or retypes a column

### Requirement: Configuration is supplied, never discovered

Every external endpoint, credential, and tunable limit SHALL be read from a single validated configuration object assembled at startup from the environment. Layers below the composition step SHALL receive what they need as arguments rather than reading the environment.

Startup SHALL fail loudly when a required setting is missing or malformed.

#### Scenario: No environment access outside configuration assembly

- **WHEN** the source tree is searched for direct environment-variable reads
- **THEN** the only matches are inside the configuration module

#### Scenario: Missing required setting stops startup

- **WHEN** the service starts without a required setting
- **THEN** startup aborts with an error naming the missing setting, and no request is served

### Requirement: Architecture is documented and kept current

The repository SHALL contain an architecture document describing the layers, the dependency rule and how it is enforced, every port that exists in code, every seam that is planned but deliberately not yet built, the lifecycle of a review from trigger to publication, and the decisions deferred.

The document SHALL be updated in the same change that alters the structure it describes.

#### Scenario: Document covers every declared port

- **WHEN** the architecture document is compared against the ports declared in code
- **THEN** every port appears in the document with its purpose and its adapters

#### Scenario: Document covers each deferred seam

- **WHEN** a seam is designed but not committed as code
- **THEN** the document states its purpose, the adapter intended to satisfy it first, and what would have to change to introduce it

#### Scenario: Structural change updates the document

- **WHEN** a change adds, removes, or renames a layer or a port
- **THEN** the same change updates the architecture document accordingly
