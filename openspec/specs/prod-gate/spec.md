# prod-gate Specification

## Purpose

The default prod gate (`gate_mechanism: environment`, decisions D2, D9, D15(a)): every prod apply
waits for an independent reviewer through the required reviewers of the GitHub `prod` environment.
ayasee refuses to run when that gate would not actually hold, and records who approved. Code:
`src/ayasee/reviewgate.py` (`require-reviewers`), `src/ayasee/approvals.py`.

## Requirements

### Requirement: Every prod run is approved
The system SHALL require an approval for every prod run that has changes (`gate: always`); `gate: risky` SHALL be refused as not supported.

#### Scenario: Low-risk change
- **WHEN** the prod plan contains only tier `none` changes
- **THEN** the `prod` job still waits for a reviewer

### Requirement: Refuse a gate that would not wait
Before `prod-plan`, the `prod-gate` job SHALL run `ayasee require-reviewers` on the (prefixed) `prod` environment with an `actions: read` token and no environment, and SHALL refuse when the environment has no required reviewers, does not exist yet, or the repository's plan ignores environment reviewers (a private repository on GitHub Free, detected from a 403 of the rulesets API), and when the API cannot be read. `solo_mode` SHALL NOT lift this check. With `gate_mechanism: workflow` the check SHALL be skipped.

#### Scenario: Private repository on GitHub Free
- **WHEN** `iac-staged` runs with the environment gate in a private repository on GitHub Free
- **THEN** `prod-gate` fails and no prod plan is produced

#### Scenario: Missing prod environment
- **WHEN** the `prod` environment does not exist
- **THEN** `prod-gate` refuses, because GitHub would create it unprotected on first use

### Requirement: Approvers are read from GitHub and recorded
The `prod` job SHALL, right after the guard and before downloading the artifact, read the approvals of the `prod` environment for this run (`ayasee approvals list --env prod`): every distinct login with an `approved` entry, sorted and comma-joined, excluding rejections. It SHALL fail closed when there is none and SHALL store the string in the ledger as `approver`.

#### Scenario: Two approvers
- **WHEN** `bob` and `alice` approved the `prod` environment for the run
- **THEN** the ledger record's `approver` is `alice,bob`

#### Scenario: No approval entry
- **WHEN** the approvals API returns no `approved` entry for `prod`
- **THEN** the `prod` job fails before anything is applied

### Requirement: Reviewers approve once, after the plan exists
`prod-plan` and `prod-in-sync` SHALL run in the `prod-plan` environment, which has no reviewers, so that reviewers approve only the `prod` job, after the plan has been produced and scanned.

#### Scenario: Gate summary before approval
- **WHEN** a reviewer opens the waiting `prod` job
- **THEN** the risk-gate summary of the plan is already in the `prod-plan` job summary

### Requirement: Independent approval
The environment gate SHALL rely on `prevent_self_review: true` and no admin bypass on `prod` (D2), so the person who triggered the run cannot approve it; `solo_mode: true` SHALL be the only opt-out and SHALL be labelled NON-COMPLIANT.

#### Scenario: Merger tries to approve
- **WHEN** the person whose merge started the run tries to approve `prod`
- **THEN** GitHub refuses the self-review
