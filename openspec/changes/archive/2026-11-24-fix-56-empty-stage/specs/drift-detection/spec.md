## MODIFIED Requirements

### Requirement: Plan at the live SHA
For each configured stage, the drift job SHALL read the environment's ledger record, skip with a notice when there is none or when it records no units, check out the live SHA (not `main`), verify the checkout with `expect-sha`, and plan every unit in binding mode `drift` (`plan -detailed-exitcode`, normal state lock and lock timeout), deleting plan files once the JSON exists and never applying.

#### Scenario: Undeployed merge
- **WHEN** a commit is merged to `main` but not yet live in `uat`
- **THEN** the `uat` drift plan does not show its changes

#### Scenario: Live record without units
- **WHEN** the live `dev` record has no units
- **THEN** the `dev` drift job skips with a notice and plans nothing
