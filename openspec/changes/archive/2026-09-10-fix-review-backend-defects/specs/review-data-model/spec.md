## MODIFIED Requirements

### Requirement: Published comments are tracked

The system SHALL record every comment it publishes to the version-control host, linking it to the finding it came from where one exists, and storing the provider's identifier for the published comment, its kind, and when it was published.

A finding SHALL be published at most once per review run.

A summary comment SHALL be published at most once per review run. Because a summary carries no originating finding, the absence of a finding SHALL NOT exempt it from that limit.

#### Scenario: Publication is recorded

- **WHEN** a comment is published to the host
- **THEN** a record stores the provider's comment identifier, the originating finding where applicable, the comment kind, and the publication time

#### Scenario: A finding cannot be recorded as published twice in one run

- **WHEN** a second publication record is written for a finding already published in that review run
- **THEN** the write is refused and the original record stands

#### Scenario: Summary comment has no finding

- **WHEN** the run's overall summary comment is published
- **THEN** it is recorded as a summary-kind comment with no originating finding

#### Scenario: A summary cannot be recorded as published twice in one run

- **WHEN** the publication step is retried and writes a second summary record for a run that already has one
- **THEN** the write is refused and the original summary record stands

#### Scenario: Two runs of the same change request each keep their summary

- **WHEN** a second review run for the same change request publishes its own summary
- **THEN** both summary records exist, one per run
