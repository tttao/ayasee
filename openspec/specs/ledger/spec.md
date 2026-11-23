# ledger Specification

## Purpose

The ledger is the source of truth for what is live in each environment of each repository (D5). It
is written by the stage's own identity to a backend store, read by the guard, the nightly drift
check and the cockpit, and projected to GitHub Deployments for humans. Code: `src/ayasee/ledger.py`,
`src/ayasee/s3.py`, `src/ayasee/trunkguard.py`, `actions/ledger`. Reference:
`docs/concepts/ledger.md`.

## Requirements

### Requirement: Record content
A ledger record SHALL carry `repo`, `env`, `sha`, `run_id`, `run_attempt`, per-unit `status` (`applied`, `no_changes`, `failed`, `skipped`) and `converged`, an overall `converged` true only if every unit converged, `approver` (or null), `breakglass_incident` (or null), `requester` only on a workflow-gate break-glass record, and `recorded_at` in RFC 3339 UTC with microseconds. Records SHALL never carry plan values, and empty `units` SHALL be refused.

#### Scenario: Partial failure
- **WHEN** the second of three units fails to apply
- **THEN** the record lists the first as `applied`, the second as `failed`, and `converged` is false

### Requirement: Store selection
The store SHALL be selected by the `AYASEE_LEDGER_URL` variable, falling back to `ledger.url`, and the run SHALL fail if neither is set. Supported URLs SHALL be `azure-blob://` (read/write, `DefaultAzureCredential`), `git-branch://` (read/write, orphan branch), `s3://` (read/write, experimental), `github-branch://` (read-only, contents API; `append` always raises) and `file://` (read/write, tests).

#### Scenario: Cockpit store is read-only
- **WHEN** `append` is called on a `github-branch://` store
- **THEN** it raises

### Requirement: Layout and idempotent writes
All stores SHALL use `{prefix}/{owner}/{name}/{env}/current.json` and `{prefix}/{owner}/{name}/{env}/history/{YYYYMMDDTHHMMSSffffffZ}-{sha[:12]}.json`, validate repository and environment names as path segments (rejecting `..`), write the history entry first and then `current.json`, succeed when retrying identical bytes, and fail when a history entry exists with different content.

#### Scenario: Retry of the same record
- **WHEN** a ledger write is retried with the same record
- **THEN** it succeeds without creating a second entry

### Requirement: Newest-of read rule
`current()` SHALL return the newer of `current.json` and the newest history entry by `recorded_at`, and `append` SHALL replace `current.json` only when the existing one is not strictly newer.

#### Scenario: Crash between writes
- **WHEN** a history entry was written but `current.json` was not
- **THEN** the next read returns the history entry

#### Scenario: Late retry of an older run
- **WHEN** an older record is appended after a newer one
- **THEN** `current.json` keeps the newer record

### Requirement: git-branch store isolation
The `git-branch://` store SHALL write with git plumbing and a temporary index without touching the caller's working tree, index or `HEAD`, SHALL refuse to run if the branch is checked out, SHALL retry a rejected push up to 5 times, and SHALL author commits as `ayasee <ayasee@users.noreply.github.com>`.

#### Scenario: Concurrent writer
- **WHEN** a push is rejected because another run wrote first
- **THEN** the store fetches, recommits and pushes again, up to 5 times

### Requirement: s3 store safety
The `s3://` store SHALL sign requests with SigV4 using only `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and optional `AWS_SESSION_TOKEN` from the environment, refuse plain `http` endpoints and unknown parameters, use `If-None-Match: *` for history entries and `If-Match`/`If-None-Match` for `current.json` with up to 5 re-reads on 412 or 409, cap decompressed bodies (1 MiB object, 16 MiB listing), refuse a listing with a DTD, and name only the HTTP status and S3 error code in errors.

#### Scenario: Conditional write lost a race
- **WHEN** the `current.json` PUT returns 412
- **THEN** the store re-reads and decides again under the newest-of rule

### Requirement: Dedicated ledger credentials
The reusable workflows SHALL pass `AYASEE_LEDGER_AWS_ACCESS_KEY_ID` and `AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY` only to the steps that read or write the ledger (guard and ledger steps of `iac-staged`, the live-SHA read of `iac-nightly`, the rollback check and ledger step of `iac-breakglass`), as `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, and those steps SHALL receive no state or provider credential.

#### Scenario: Plan step
- **WHEN** a plan step runs with an `s3://` ledger
- **THEN** it does not receive the ledger key

### Requirement: Deployments projection
With `--mirror-deployments`, a ledger write SHALL also create a GitHub deployment whose `ref` is the commit SHA (payload `branch = "main"`), `production_environment` true for `prod`, and a `success`/`failure` status by convergence with a log URL; the mirror SHALL be refused unless `GITHUB_REF` is `refs/heads/main`, and a mirror failure after the write SHALL report "ledger written, but deployment mirror failed". Nothing in ayasee SHALL read Deployments as authority.

#### Scenario: Mirror from a branch
- **WHEN** a ledger write with `--mirror-deployments` runs with `GITHUB_REF=refs/heads/feature`
- **THEN** the mirror is refused

### Requirement: Ledger token cannot bypass main
When `ledger-token` is passed, the `resolve` job of `iac-staged` and `iac-nightly` SHALL run `ayasee trunk-guard` with read-only calls, pass when an active ruleset with a `pull_request` rule applies to `main` and the token has no bypass for it, and otherwise emit a `::warning::` and a run-summary line (unprotected `main`, classic protection that cannot be verified, inactive ruleset, a bypass, an unknown value or an API error), never reporting unverified as protected.

#### Scenario: Token can bypass the ruleset
- **WHEN** the ruleset on `main` lists the ledger token's actor as `always` bypass
- **THEN** the run warns that the ledger token can push to `main`
