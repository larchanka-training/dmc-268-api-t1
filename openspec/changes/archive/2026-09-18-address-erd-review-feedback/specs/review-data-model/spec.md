## ADDED Requirements

### Requirement: Change request persistence

The system SHALL persist each change request (pull request or merge request) it is asked to review, recording its repository, the provider's number for it, title, description, author, source and target branch, latest known head commit, and state.

A change request SHALL be uniquely identified by its repository together with the provider's number.

A change request's state SHALL be one of: open, closed, or merged. Provider-specific states SHALL be translated to this set before they are stored.

The head commit on a change request SHALL mean the most recent head the system has seen for it. The commit a particular review covered SHALL be recorded on that review run and SHALL NOT change when the change request's head moves on.

#### Scenario: Change request is stored with its metadata

- **WHEN** a change request is submitted for review
- **THEN** a record exists linking it to its repository and carrying its number, title, description, author, branches, head commit, and state

#### Scenario: New commit updates the head

- **WHEN** a new commit is pushed to a tracked change request
- **THEN** the record's head commit is updated and the record is not duplicated

#### Scenario: Earlier run keeps the commit it reviewed

- **WHEN** a change request's head commit is updated after a review run was created
- **THEN** that review run still reports the commit it was created for

#### Scenario: State outside the defined set is refused

- **WHEN** a change request is written with a state other than open, closed, or merged
- **THEN** the write is refused and no record with that state exists

#### Scenario: Deleting a repository with change requests is refused

- **WHEN** deletion is attempted for a repository that still has change requests
- **THEN** the deletion is refused and the repository and its change requests remain unchanged

### Requirement: Review history is never deleted implicitly

Deleting a stored record SHALL NOT remove any record that depends on it. A deletion that would leave dependent records behind SHALL be refused, whatever the calling code path, including a direct database session.

This protects the provider's identifiers for comments already published to the host, which the system needs to update or remove those comments later, and the cost figures recorded on review runs.

Removing a record together with its dependants SHALL require an explicit operation that deletes each dependant first. No such operation is part of this requirement.

#### Scenario: Deleting a change request with review runs is refused

- **WHEN** deletion is attempted for a change request that has review runs
- **THEN** the deletion is refused and the change request and its runs remain

#### Scenario: Deleting a review run with dependants is refused

- **WHEN** deletion is attempted for a review run that has context payloads, findings, or published comments
- **THEN** the deletion is refused and all of those records remain

#### Scenario: Deleting a published finding is refused

- **WHEN** deletion is attempted for a finding that has a published comment record
- **THEN** the deletion is refused and the published comment keeps its provider comment identifier

#### Scenario: Record without dependants can be deleted

- **WHEN** deletion is attempted for a record that nothing depends on
- **THEN** the record is removed

## REMOVED Requirements

### Requirement: Change request tracking

**Reason**: Its scenario "Deleting a repository removes its change requests" contradicts the new rule that deletion never removes dependants, and a MODIFIED block cannot drop a scenario. The rest of the requirement carries over unchanged, with the state constrained and the head commit defined.

**Migration**: Replaced by "Change request persistence" in this capability. Deletion behaviour is now specified by "Review history is never deleted implicitly".
