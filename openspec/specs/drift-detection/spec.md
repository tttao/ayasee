# drift-detection Specification

## Purpose

`iac-nightly` (`.github/workflows/iac-nightly.yml`) checks every configured environment for drift
against the SHA the ledger says is live, keeps one drift issue per environment, and lets prod drift
hold the next prod rollout. Code: `src/ayasee/drift.py`, `src/ayasee/issues.py`. Reference:
`docs/concepts/drift.md`.
## Requirements
### Requirement: Plan at the live SHA
For each configured stage, the drift job SHALL read the environment's ledger record, skip with a notice when there is none or when it records no units, check out the live SHA (not `main`), verify the checkout with `expect-sha`, and plan every unit in binding mode `drift` (`plan -detailed-exitcode`, normal state lock and lock timeout), deleting plan files once the JSON exists and never applying.

#### Scenario: Undeployed merge
- **WHEN** a commit is merged to `main` but not yet live in `uat`
- **THEN** the `uat` drift plan does not show its changes

#### Scenario: Live record without units
- **WHEN** the live `dev` record has no units
- **THEN** the `dev` drift job skips with a notice and plans nothing

### Requirement: Skip environments with an open apply failure
The drift job SHALL skip an environment with a notice when an open issue labelled `ayasee:apply-failed` and `env:<env>` exists.

#### Scenario: Half-applied state
- **WHEN** `uat` has an open apply-failed issue
- **THEN** no drift plan runs for `uat`

### Requirement: Exit codes are never read as no drift
Exit 2 SHALL mean changes, and exit 1 or any other value SHALL be an error that fails the job and sends `stage_failed` for `drift-<env>`.

#### Scenario: Provider error
- **WHEN** a drift plan exits 1
- **THEN** the job fails and no issue is closed

### Requirement: What counts as drift
`ayasee drift evaluate` SHALL count only managed resources with a real change: create, delete, replace, import, forget and pending moves are always drift; an update is noise only when every differing attribute path falls under a `drift_noise` path of its type (read from the configuration at the live SHA, dotted paths with numeric list indexes); an update whose paths cannot be determined is drift; a changed root output is drift reported as `output.<name>` without its value.

#### Scenario: Noise path
- **WHEN** `drift_noise` lists `access_control` for `databricks_permissions` and only `access_control.0.permission_level` differs
- **THEN** the unit has no drift

### Requirement: One issue per environment
`ayasee drift sync` SHALL keep one issue titled `drift: <env>` with labels `ayasee:drift` and `env:<env>` listing drifted addresses as `<unit>: <address>` (never values) and the run link; a later night with drift SHALL comment, and a night without drift SHALL comment `drift cleared at <sha>` and close it.

#### Scenario: Drift resolved
- **WHEN** the next night finds no drift in an environment with an open drift issue
- **THEN** the issue is commented and closed and `drift_cleared` is sent

### Requirement: Prod drift holds prod
For `prod` the drift issue SHALL also carry `ayasee:stop-the-line`, so the guard refuses `prod-plan` and `prod` while it is open.

#### Scenario: Prod drift found
- **WHEN** drift is found in prod
- **THEN** the next staged run stops at the prod-plan guard with `stop_the_line`

### Requirement: Notify on transitions only
Teams SHALL get `drift_found` when a run newly opens an issue and `drift_cleared` when it closes one; repeated nights SHALL not notify again.

#### Scenario: Second night of drift
- **WHEN** drift persists on a second night
- **THEN** the issue gets a comment and no notification is sent

### Requirement: Plan-only drift identities
Every drift job SHALL run in environment `drift-<env>` (main only, no reviewers) with `id-token: write`, `contents: read`, `issues: write`, `actions: read`, in concurrency group `ayasee-drift-<repo>-<env>` with `cancel-in-progress: false`; its identity SHALL hold read and state-lease rights and no apply rights.

#### Scenario: Nightly run
- **WHEN** the nightly cron fires
- **THEN** no drift job waits for a reviewer

