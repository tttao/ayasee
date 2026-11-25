## MODIFIED Requirements

### Requirement: Record content
A ledger record SHALL carry `repo`, `env`, `sha`, `run_id`, `run_attempt`, per-unit `status` (`applied`, `no_changes`, `failed`, `skipped`) and `converged`, an overall `converged` true only if every unit converged, `approver` (or null), `breakglass_incident` (or null), `requester` only on a workflow-gate break-glass record, and `recorded_at` in RFC 3339 UTC with microseconds. Records SHALL never carry plan values, and empty `units` SHALL be refused, except for the record of a stage without units, written only with `ayasee ledger write --no-units` (ledger action input `no-units`): empty `units`, converged, and refused together with `--units-json`, `--approver`, `--breakglass-incident` or `--requester`.

#### Scenario: Partial failure
- **WHEN** the second of three units fails to apply
- **THEN** the record lists the first as `applied`, the second as `failed`, and `converged` is false

#### Scenario: Empty units file
- **WHEN** `ayasee ledger write` is given a `--units-json` file holding `{}`
- **THEN** it fails and writes nothing

#### Scenario: Stage without units
- **WHEN** `ayasee ledger write --no-units` runs for a stage
- **THEN** the record has empty `units` and `converged` true

#### Scenario: No units with an approver
- **WHEN** `ayasee ledger write --no-units --approver bob` runs
- **THEN** it fails and writes nothing
