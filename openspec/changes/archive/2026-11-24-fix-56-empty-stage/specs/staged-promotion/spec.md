## MODIFIED Requirements

### Requirement: Pre-prod stages plan and apply in one job
Each pre-prod stage job SHALL run setup, the guard, a plan, and an apply in binding mode `same-job` with plan encryption off, applying unit by unit in dependency order with a converge check after each unit, and SHALL then write the stage's ledger record. The plan SHALL pass `allow-no-units: true`; when the stage's units directory holds no unit, the job SHALL skip the apply, emit a notice, and write the stage's ledger record at the SHA with no units (`no-units`), converged.

#### Scenario: Unit fails to converge
- **WHEN** the post-apply re-plan of a unit is not empty
- **THEN** the run stops, later units are skipped, the ledger is written with the units that were applied, and the job fails

#### Scenario: Stage without units
- **WHEN** `<units_root>/<stage>` exists but holds no unit
- **THEN** nothing is applied, the ledger records the stage at this SHA with no units and converged, and the next stage's guard passes

### Requirement: Prod plan job
`prod-plan` SHALL run in its own `prod-plan` environment with only `contents: read`, `id-token: write` and `issues: read`, run the guard, plan every prod unit in binding mode `encrypted_plan` with `allow-no-units: true`, refuse a cascading change, run the Rego packs and a Trivy config scan (HIGH and CRITICAL fail) on the plan JSON inside the job, upload only the encrypted plan files and `manifest.json` as artifact `ayasee-prod-plan` (7 days), and output `has-changes`, `no-units`, `critical`, `high` and `units`. When prod has no units it SHALL skip the scans, the upload and the `prod_waiting` notification and output `no-units` true, `has-changes` false and an empty `units` list. It SHALL write no ledger record, deployment or issue.

#### Scenario: Policy finding
- **WHEN** a Rego `deny` or a HIGH Trivy finding is reported on the prod plan
- **THEN** the run stops before anything is uploaded

#### Scenario: Output-only change
- **WHEN** the only change in the prod plan is a root output
- **THEN** `has-changes` is `true` and prod goes through the gate

#### Scenario: Prod without units
- **WHEN** `<units_root>/prod` exists but holds no unit
- **THEN** `prod-plan` succeeds with `no-units` true, uploads nothing, and neither `prod` nor `prod-approval` runs

### Requirement: Empty plan path
The system SHALL, when every prod unit plans clean and the gate settings did not change, skip the gated prod job and run `prod-in-sync` in the `prod-plan` environment, which runs the guard and writes the prod ledger record (and deployment projection) with every unit `no_changes` and converged; an empty `units` list SHALL be refused unless `prod-plan` reported `no-units`. When prod has no units, `prod-in-sync` SHALL run whether or not the gate settings changed and SHALL write the prod record with no units, converged and without an approver, so the gate change still needs an approval at the first prod plan with units.

#### Scenario: Nothing changes in prod
- **WHEN** `has-changes` and `gate-changed` are both `false`
- **THEN** no reviewer is asked and the prod ledger records the new SHA as in sync

#### Scenario: First push before any unit
- **WHEN** the first push to `main` has no unit in any stage, with `gate_mechanism: workflow` and no approved prod apply yet
- **THEN** every pre-prod stage and prod are recorded at the SHA with no units, no approval issue is opened, and the run succeeds
