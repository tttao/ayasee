# breakglass Specification

## Purpose

`iac-breakglass` (`.github/workflows/iac-breakglass.yml`, decision D3, DORA RTS Art. 17(1)(f)-(g))
is the one emergency path into prod: an incident reference, a separate `prod-breakglass` environment
and reviewer set, the policy pack, the whole prod tree, and a post-approval issue. With the workflow
gate it becomes a request approved through `iac-approve`. Reference:
`docs/compliance/dora-art17.md`.

## Requirements

### Requirement: Validated inputs
The system SHALL run break-glass only from `main` and SHALL refuse an `incident` that is not of the form `ABC-123` or `INC123`, and a `sha` that is not a full 40-character commit that is an ancestor of `origin/main`.

#### Scenario: Unmerged commit
- **WHEN** `sha` is a commit not on `main`
- **THEN** the run is refused before any plan

### Requirement: Rollback rule
The system SHALL read the live prod SHA from the ledger and refuse a `sha` older than it (the live SHA is not an ancestor of `sha`) unless `allow-rollback` is `true`, in which case it SHALL log a `rollback` warning and add a "Rollback from `<live sha>`" line to the post-approval issue. With no prod ledger record the check SHALL be skipped with a notice.

#### Scenario: Rollback without flag
- **WHEN** `sha` is older than the live prod SHA and `allow-rollback` is `false`
- **THEN** the run is refused

### Requirement: Whole prod tree, separate environment
Break-glass SHALL plan and apply the whole prod tree (no unit scope, R71) in the `prod-breakglass` environment with its own identity and reviewers, using binding mode `breakglass` with the same plan encryption and lock timeout as the normal prod stage, after `ayasee require-reviewers` confirms `prod-breakglass` has required reviewers.

#### Scenario: Unit scope requested
- **WHEN** the plan action receives `only-units` in `breakglass` mode
- **THEN** it refuses

### Requirement: Policy pack is the pre-apply machine check
Break-glass SHALL run the Rego packs with stage `prod` on the plan before applying, a `deny` SHALL fail the run before the apply, and the applied per-unit plans SHALL match the checked preview's `(address, actions)`. Trivy SHALL NOT run on this path.

#### Scenario: Policy deny
- **WHEN** the break-glass plan triggers `SG-DBX-001`
- **THEN** nothing is applied

### Requirement: No stop-the-line enforcement
Break-glass SHALL NOT run the guard, so an emergency fix can ship while a stage is red; a failed apply SHALL still open the prod `ayasee:stop-the-line` issue with `ayasee:apply-failed`.

#### Scenario: Prod held by drift
- **WHEN** an open stop-the-line issue exists for prod
- **THEN** break-glass still applies

### Requirement: Ledger and post-approval issue
Break-glass SHALL read the approvers (failing when there are none), write the prod ledger record with the incident and approvers (also when cancelled after the apply started), open an issue titled `post-approval: break-glass <incident>` with labels `ayasee:post-approval` and `env:prod` listing the incident, the short SHA, the run link, the approvers, the change counts and the checklist (independent review, incident linked, follow-up PR merged, drift clean) on every use, and notify `breakglass_used`.

#### Scenario: Apply fails
- **WHEN** the break-glass apply fails
- **THEN** the ledger is written, the stop-the-line issue is opened and the post-approval issue is still opened

### Requirement: Request and approval with the workflow gate
When `main` has `gate_mechanism: workflow`, `iac-breakglass` SHALL refuse unless `main` lists `reviewers.breakglass`, and SHALL only make a request: plan the whole prod tree in `prod-plan` with the plan identity (`encrypted_plan`), run the policy pack and the rollback rule, keep the plan, manifest and `request.json` as `ayasee-breakglass-plan-<sha>` (7 days), and open a `break-glass approval: <incident> <sha>` issue labelled `ayasee:breakglass-approval` and `env:prod`. Nothing SHALL be applied by the request.

#### Scenario: No breakglass reviewers
- **WHEN** `gate_mechanism` is `workflow` and `reviewers.breakglass` is empty on `main`
- **THEN** `iac-breakglass` refuses to run

### Requirement: Break-glass approval checks
`iac-approve` with an `incident` SHALL apply the request only when the actor is in `reviewers.breakglass` at `main` and at the last approved apply, is not the requester and did not write or merge the change or any commit since the anchor (unless `solo_mode` at both commits), the plan is the newest of a successful `iac-breakglass` dispatch on `main` with exactly the given digest, `request.json` names the same incident and commit, and no prod ledger record is as new as or newer than the start of the request run. It SHALL then apply in `prod-breakglass` without the guard and record the incident, the approver and the `requester` in the ledger.

#### Scenario: Requester approves own request
- **WHEN** the requester starts `iac-approve` for their own request without `solo_mode`
- **THEN** `verify` refuses

#### Scenario: Prod changed since the request
- **WHEN** another apply recorded prod after the request run started
- **THEN** the approval is refused and the request must be made again
