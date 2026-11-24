## MODIFIED Requirements

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
