# stage-guard Specification

## Purpose

`ayasee guard` (`src/ayasee/guard.py`, `actions/guard`) decides from the ledger and the open issues
whether a stage may run for a given SHA. It enforces stage order, supersession and stop-the-line,
and fails closed.
## Requirements
### Requirement: Predecessor must have this SHA live and converged
The guard SHALL allow a stage only when the preceding recordable stage (in the configured `stages` order, skipping `prod-plan`, which writes no record) has a ledger record at this SHA that converged. A record of a stage without units counts as converged at its SHA, for this rule and for supersession.

#### Scenario: Predecessor has no record
- **WHEN** the guard runs for `uat` and `dev` has no ledger record
- **THEN** it refuses with `predecessor_missing`

#### Scenario: Predecessor not converged
- **WHEN** the predecessor's record is at this SHA but not converged
- **THEN** it refuses with `predecessor_not_converged`

#### Scenario: Predecessor has no units
- **WHEN** `uat` holds no unit and its record at this SHA has no units
- **THEN** the guard allows `prod-plan`, and refuses an older SHA at the prod apply as `superseded`

### Requirement: Supersession
The guard SHALL refuse with `superseded` when the predecessor's record is at a different SHA and converged, and SHALL refuse with `predecessor_missing` when that different-SHA record did not converge. The `prod` job SHALL run the guard again after the approval wait, at apply time.

#### Scenario: Newer commit reached uat during approval
- **WHEN** a run is approved at the prod gate after a newer commit converged in `uat`
- **THEN** the guard in `prod` refuses the apply as `superseded` and nothing is applied

### Requirement: Stop-the-line holds prod
For `prod-plan` and `prod`, the guard SHALL refuse with `stop_the_line` while an open `ayasee:stop-the-line` issue exists for any environment up to and including prod.

#### Scenario: Open prod drift
- **WHEN** an open drift issue for `prod` carries `ayasee:stop-the-line`
- **THEN** the guard refuses to start `prod-plan`

### Requirement: Fail closed on infrastructure errors
The guard SHALL exit 1 on any infrastructure error (ledger unreadable, issues API failure) and never treat it as a pass.

#### Scenario: Ledger store unreachable
- **WHEN** the ledger store cannot be read
- **THEN** the guard exits 1 and the stage does not run

### Requirement: Guard uses logical stage names
The guard SHALL key its predecessor chain and its stop-the-line lookup on the logical stage names (unprefixed `env:<stage>` labels), independent of `environment_prefix` and of which job slot ran the stage.

#### Scenario: Prefixed environments
- **WHEN** `environment_prefix` is `iac-`
- **THEN** the guard still reads the ledger records keyed `dev`, `uat` and `prod`

