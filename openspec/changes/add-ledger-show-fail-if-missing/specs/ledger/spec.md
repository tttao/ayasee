## ADDED Requirements

### Requirement: Show command and missing records
`ayasee ledger show` SHALL print the current record (or, with `--history N`, the newest N history records) as JSON; when there is no record it SHALL print `null` (or `[]`) and exit 0, unless `--fail-if-missing` is given, in which case it SHALL print `ayasee: no ledger record for <repo> <env>` to stderr, print nothing to stdout, and exit 1.

#### Scenario: Missing record without the flag
- **WHEN** `ayasee ledger show` runs for an environment with no record
- **THEN** it prints `null` and exits 0

#### Scenario: Missing record with the flag
- **WHEN** `ayasee ledger show --fail-if-missing` runs for an environment with no record
- **THEN** it prints `ayasee: no ledger record for <repo> <env>` to stderr and exits 1

#### Scenario: Empty history with the flag
- **WHEN** `ayasee ledger show --history 5 --fail-if-missing` runs for an environment with no history
- **THEN** it exits 1 with the same message

#### Scenario: Record present with the flag
- **WHEN** `ayasee ledger show --fail-if-missing` runs for an environment that has a record
- **THEN** it prints the record as JSON and exits 0
