# review-data-model Specification

## Purpose
Defines the persistent domain model of the automated review pipeline: the entities that outlive a single request, how they relate and are identified, the states a review run moves through, and the invariants that keep a stored finding traceable back to a real line of a real diff.

## Requirements

### Requirement: Repository registration

The system SHALL persist each version-control repository it reviews, recording the hosting provider, the provider's own identifier for the repository, its human-readable full name, its default branch, and whether automatic review is enabled.

A repository SHALL be uniquely identified by the pair of provider and provider-side identifier.

#### Scenario: Repository is stored on first registration

- **WHEN** a repository is registered for review
- **THEN** a repository record exists carrying its provider, provider-side identifier, full name, default branch, and automatic-review flag

#### Scenario: Re-registering the same repository does not duplicate it

- **WHEN** the same provider and provider-side identifier are registered a second time
- **THEN** the existing record is updated and no second record is created

#### Scenario: Same name on two providers is allowed

- **WHEN** repositories with an identical full name exist on two different providers
- **THEN** both records are stored and remain distinguishable by provider

### Requirement: Change request tracking

The system SHALL persist each change request (pull request or merge request) it is asked to review, recording its repository, the provider's number for it, title, description, author, source and target branch, current head commit, and state.

A change request SHALL be uniquely identified by its repository together with the provider's number.

#### Scenario: Change request is stored with its metadata

- **WHEN** a change request is submitted for review
- **THEN** a record exists linking it to its repository and carrying its number, title, description, author, branches, head commit, and state

#### Scenario: New commit updates the head

- **WHEN** a new commit is pushed to a tracked change request
- **THEN** the record's head commit is updated and the record is not duplicated

#### Scenario: Deleting a repository removes its change requests

- **WHEN** a repository record is deleted
- **THEN** its change requests, and everything belonging to them, are removed with it

### Requirement: Review run lifecycle

The system SHALL persist a review run for each attempt to review a change request at a specific head commit, recording which change request and commit it covers, what triggered it, its state, its timestamps, and — once finished — the model used and the token and duration cost.

A review run SHALL occupy exactly one of the states: queued, building context, analysing, publishing, completed, failed, or cancelled.

#### Scenario: Run is created in the queued state

- **WHEN** a review is triggered
- **THEN** a review run record is created in the queued state, bound to the change request and the head commit being reviewed, and recording its trigger

#### Scenario: Terminal state is final

- **WHEN** a review run has reached completed, failed, or cancelled
- **THEN** any further attempt to change its state is rejected and the stored state is unchanged

#### Scenario: Failure records its cause

- **WHEN** a review run ends in the failed state
- **THEN** the record carries a failure reason and the timestamp at which it failed

#### Scenario: Completion records cost

- **WHEN** a review run reaches the completed state
- **THEN** the record carries the model identifier, the tokens consumed, and the wall-clock duration of the run

### Requirement: One run per commit unless re-review is requested

The stored model SHALL prevent a change request from accumulating more than one non-terminal review run for the same head commit. A run that has reached a terminal state SHALL NOT block a later run for the same commit, so an explicitly requested re-review remains possible.

#### Scenario: Second active run for the same commit is refused

- **WHEN** a review run is created for a change request and head commit that already has a run in a non-terminal state
- **THEN** the write is refused and only the original run exists

#### Scenario: Re-review after completion is allowed

- **WHEN** a review run is created for a head commit whose previous run reached a terminal state
- **THEN** the new run is stored and the earlier run remains readable

#### Scenario: History is preserved across re-reviews

- **WHEN** a change request is reviewed again after a new commit
- **THEN** a new review run record is created and the earlier runs remain readable

### Requirement: A run cannot block its commit forever

Because at most one non-terminal run may exist per change request and head commit, a run that stops making progress would otherwise block that commit permanently. The stored model SHALL make an abandoned run recoverable without manual database surgery.

Every review run SHALL record when it last changed state. A run that has not changed state for longer than a configured limit SHALL be movable to the failed state, which releases the commit for a new run.

#### Scenario: Abandoned run is identifiable

- **WHEN** review runs are queried for staleness
- **THEN** every non-terminal run reports how long it has been in its current state

#### Scenario: Stale run releases its commit

- **WHEN** a non-terminal run has exceeded the staleness limit and is moved to failed
- **THEN** a new run for the same change request and head commit can be created

#### Scenario: Progress resets the clock

- **WHEN** a run advances from one non-terminal state to another
- **THEN** its last-progress timestamp is updated and it is no longer stale

### Requirement: Repeated delivery of the same job is harmless

A review run SHALL be claimable by exactly one worker. Delivering the same job twice SHALL NOT produce two concurrent executions or two sets of findings.

#### Scenario: Second worker loses the claim

- **WHEN** two workers receive the same job and both attempt to move the run out of the queued state
- **THEN** one succeeds and the other is refused, leaving a single execution

#### Scenario: Redelivery after completion is ignored

- **WHEN** a job is delivered again for a run that already reached a terminal state
- **THEN** no work is performed and no additional findings are stored

### Requirement: Context payload persistence

The system SHALL persist the context assembled for a review run — the material sent to the model — recording which run it belongs to, which files it covers, the context tiers it includes, its size in tokens, and a content digest.

Context payloads SHALL be reusable: an assembled payload for a given change request and head commit SHALL be retrievable without reassembly.

#### Scenario: Assembled context is stored with its run

- **WHEN** context assembly finishes for a review run
- **THEN** a context payload record is stored against that run with its file list, tiers, token count, and content digest

#### Scenario: Identical context is recognised

- **WHEN** context assembled for a later run has the same content digest as a stored payload
- **THEN** the stored payload is reusable and reassembly is unnecessary

#### Scenario: Oversized context is recorded as chunks

- **WHEN** assembled context exceeds the model's context budget and is split
- **THEN** each chunk is stored as its own payload record bound to the same run and ordered within it

### Requirement: Findings are anchored to the diff

The system SHALL persist each finding a review produces, recording its review run, file path, the line coordinates it refers to, its category, its severity, its message, an optional replacement suggestion, and the model's confidence.

Every stored finding SHALL refer to a line that is part of the change request's diff. A finding whose coordinates fall outside the diff SHALL be rejected rather than stored.

#### Scenario: Finding inside the diff is stored

- **WHEN** a finding refers to a line that the diff adds or modifies
- **THEN** it is stored with its file path, line coordinates, category, severity, message, optional suggestion, and confidence

#### Scenario: Finding outside the diff is rejected

- **WHEN** a finding refers to a file or line absent from the diff
- **THEN** it is not stored and the rejection is recorded for the run

#### Scenario: Category is constrained

- **WHEN** a finding is stored
- **THEN** its category is one of the recognised review dimensions — security, correctness, performance, or readability — and its severity is one of the defined levels

#### Scenario: Duplicate findings are collapsed

- **WHEN** a run produces two findings with the same file, coordinates, and category
- **THEN** one finding is stored and the duplicate is discarded

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

### Requirement: Timestamps and identity

Every persisted entity SHALL carry a surrogate primary key that is not a provider-assigned value, and creation and last-update timestamps stored with time-zone information.

#### Scenario: Entity carries its own key

- **WHEN** any entity is stored
- **THEN** its primary key is generated by the system and is independent of any provider-assigned identifier

#### Scenario: Timestamps are time-zone aware

- **WHEN** an entity is created or updated
- **THEN** its creation and update timestamps are recorded with time-zone information
