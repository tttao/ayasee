# notifications Specification

## Purpose

Teams Adaptive Cards for the events people must react to, carrying counts and links only (D8), and
never able to break a run. Code: `src/ayasee/notify.py`, `actions/notify`.

## Requirements

### Requirement: Closed set of events
`ayasee notify` SHALL accept only the events `prod_waiting`, `prod_deployed`, `prod_rejected`, `prod_held`, `prod_failed`, `stage_failed`, `drift_found`, `drift_cleared`, `new_findings` and `breakglass_used`, each with a fixed title and status line.

#### Scenario: Unknown event
- **WHEN** `ayasee notify` is called with an event outside the set
- **THEN** it exits 2 for bad arguments

### Requirement: Counts and links only
Cards SHALL be built from a closed set of scalar fields (environment, counts critical/high/medium/total, links, fixed detail keys such as `replan_needed`), so that plan values, addresses and finding details cannot enter a card; the CLI SHALL take a detail key, never free text.

#### Scenario: prod_waiting
- **WHEN** a prod plan waits for approval
- **THEN** the card carries the four counts and a run link, and no resource address

### Requirement: Fail open
`ayasee notify` SHALL exit 0 when the webhook (`AYASEE_TEAMS_WEBHOOK`, from the optional `teams-webhook` secret) is unset or the post fails, and exit 2 only on bad arguments.

#### Scenario: Teams outage
- **WHEN** the webhook returns an error after a successful prod apply
- **THEN** the run still succeeds

### Requirement: Teams cannot be disabled by configuration yet
`notify.teams: false` SHALL be refused as not supported; notifications SHALL be skipped by leaving the `teams-webhook` secret unset.

#### Scenario: No webhook
- **WHEN** the caller passes no `teams-webhook`
- **THEN** no card is sent and nothing fails
