# staged-promotion Specification

## Purpose

`iac-staged` (`.github/workflows/iac-staged.yml`) is the reusable workflow that carries one commit
of `main` through every configured stage in one run: the pre-prod stages plan and apply, `prod-plan`
produces an encrypted, scanned prod plan, and a gated job applies it, or `prod-in-sync` records an
unchanged prod. Reference: `docs/concepts/staged-run.md`.

## Requirements

### Requirement: One run per commit of main, in stage order
The system SHALL promote a single commit SHA through the configured `stages` in order, running every stage job only when `github.ref` is `refs/heads/main`, using four fixed pre-prod job slots (`stage-1` to `stage-4`) filled right-aligned so that `stage-4` always runs the stage just before prod, followed by `prod-plan` and then `prod`, `prod-approval` or `prod-in-sync`.

#### Scenario: Default three stages
- **WHEN** `stages` is `[dev, uat, prod]`
- **THEN** `stage-1` and `stage-2` are skipped as unused, `stage-3` runs `dev`, `stage-4` runs `uat`, and the guard of `prod-plan` and `prod` chains from `uat`

#### Scenario: Dispatch on another branch
- **WHEN** the caller is started with `workflow_dispatch` on a branch other than `main`
- **THEN** every stage job is skipped

### Requirement: Failure stops the chain
The system SHALL count a skipped predecessor slot as passed only when `resolve` said to skip it (unused, or before `start-stage`); a predecessor skipped because its own predecessor failed SHALL stop the chain.

#### Scenario: dev fails
- **WHEN** the `dev` stage job fails
- **THEN** the `uat`, `prod-plan` and prod jobs do not run

### Requirement: Resolve refuses superseded or unsupported runs
The `resolve` job SHALL refuse the run as `superseded` when `github.sha` is no longer the tip of `main` and a later commit changes the IaC (a file inside `iac_root` other than documentation, the configuration file, or a caller `.github/workflows/iac-*.yml`), SHALL refuse on any doubt (an API error, a tip that does not descend from the SHA, or a comparison of 300 files or more), SHALL run `ayasee config check-supported`, and SHALL validate `stages` and `start-stage` with `ayasee config staged`.

#### Scenario: Application-only commit after the IaC commit
- **WHEN** a newer commit on `main` changes only files outside `iac_root`
- **THEN** the run of the older IaC commit continues at its own SHA

#### Scenario: Newer IaC commit
- **WHEN** a newer commit on `main` changes a file under `iac_root`
- **THEN** `resolve` fails with `superseded`

### Requirement: Pre-prod stages plan and apply in one job
Each pre-prod stage job SHALL run setup, the guard, a plan, and an apply in binding mode `same-job` with plan encryption off, applying unit by unit in dependency order with a converge check after each unit, and SHALL then write the stage's ledger record.

#### Scenario: Unit fails to converge
- **WHEN** the post-apply re-plan of a unit is not empty
- **THEN** the run stops, later units are skipped, the ledger is written with the units that were applied, and the job fails

### Requirement: Stop-the-line on a failed pre-prod apply
The system SHALL, when a stage apply fails, write the ledger record, fail the job, open or extend an `ayasee:stop-the-line` issue (with `ayasee:apply-failed`) for that environment, and send a `stage_failed` notification.

#### Scenario: Failed uat apply
- **WHEN** an apply in `uat` fails
- **THEN** a stop-the-line issue for `uat` is open and the guard blocks later stages until it is closed

### Requirement: Prod plan job
`prod-plan` SHALL run in its own `prod-plan` environment with only `contents: read`, `id-token: write` and `issues: read`, run the guard, plan every prod unit in binding mode `encrypted_plan`, refuse a cascading change, run the Rego packs and a Trivy config scan (HIGH and CRITICAL fail) on the plan JSON inside the job, upload only the encrypted plan files and `manifest.json` as artifact `ayasee-prod-plan` (7 days), and output `has-changes`, `critical`, `high` and `units`. It SHALL write no ledger record, deployment or issue.

#### Scenario: Policy finding
- **WHEN** a Rego `deny` or a HIGH Trivy finding is reported on the prod plan
- **THEN** the run stops before anything is uploaded

#### Scenario: Output-only change
- **WHEN** the only change in the prod plan is a root output
- **THEN** `has-changes` is `true` and prod goes through the gate

### Requirement: Empty plan path
The system SHALL, when every prod unit plans clean and the gate settings did not change, skip the gated prod job and run `prod-in-sync` in the `prod-plan` environment, which runs the guard and writes the prod ledger record (and deployment projection) with every unit `no_changes` and converged; an empty `units` list SHALL be refused.

#### Scenario: Nothing changes in prod
- **WHEN** `has-changes` and `gate-changed` are both `false`
- **THEN** no reviewer is asked and the prod ledger records the new SHA as in sync

### Requirement: Prod plan failures before apply are not incidents
A prod job that stops before anything is applied (stale or mismatched plan, init or decrypt failure) SHALL NOT open a stop-the-line issue; the job summary and a `prod_failed` notification with the status line "Plan refused: re-plan and approve again" SHALL say to re-run `prod-plan` and approve again.

#### Scenario: Binding mismatch at prod
- **WHEN** `ayasee binding verify` fails in the `prod` job
- **THEN** no stop-the-line issue is opened and the run asks for a re-plan and a new approval

### Requirement: Concurrency and lock waiting
Every stage job SHALL use the concurrency group `ayasee-<repo>-<env>` with `cancel-in-progress: false` (`prod-in-sync` shares the `prod` group), unused slots SHALL use a run-unique group, and every plan and apply that takes the state lock SHALL pass `-lock-timeout` (default `10m`).

#### Scenario: Overlapping drift plan
- **WHEN** a staged apply starts while a nightly drift plan holds the state lock
- **THEN** the apply waits up to the lock timeout instead of failing

### Requirement: Redrive from a later stage
The system SHALL accept `start-stage` as empty (the first stage), a pre-prod stage name, or `prod-plan`; earlier jobs are skipped but the guard SHALL still require the preceding stage to have the SHA live and converged.

#### Scenario: Redrive prod-plan
- **WHEN** the workflow is called with `start-stage: prod-plan` and `uat` already has the SHA live
- **THEN** the pre-prod jobs are skipped and `prod-plan` runs

### Requirement: Secrets are declared, never inherited
The reusable workflows SHALL declare every secret explicitly under `on.workflow_call.secrets` (optional `teams-webhook`, `ledger-token`, plan-key credentials, provider tokens, `AYASEE_PROVIDER_ENV`, ledger keys) and SHALL pass each only to the steps that need it.

#### Scenario: Ledger token scope
- **WHEN** `ledger-token` is passed
- **THEN** only the guard, ledger and trunk-guard steps receive it, as step-level environment with no file or global git configuration

### Requirement: Rejected prod gate is announced
The `prod-result` job SHALL run when `prod` failed and there were changes, read the run's approvals with `ayasee approvals rejected`, and post `prod_rejected` when the gate was rejected.

#### Scenario: Reviewer rejects
- **WHEN** a reviewer rejects the `prod` environment
- **THEN** a `prod_rejected` notification is sent
