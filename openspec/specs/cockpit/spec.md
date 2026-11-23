# cockpit Specification

## Purpose

The cockpit is a read-only, server-less view of an estate: repositories as rows, environments as
columns. `cockpit-collect` builds one `ayasee.snapshot/v1` JSON from ledgers and the GitHub API,
commits it with a dated history to a data repository, and a pluggable renderer presents it. It never
approves, syncs or re-drives (D8). Code: `src/ayasee/cockpit/`, `cockpit-site/`,
`actions/cockpit-site`, `.github/workflows/cockpit-collect.yml`, `schema/snapshot.v1.json`.
Reference: `docs/concepts/cockpit.md`, `docs/cockpit/setup.md`.

## Requirements

### Requirement: Snapshot contract
The collector SHALL write a snapshot with `schema: ayasee.snapshot/v1` validating against `schema/snapshot.v1.json`, carrying per repository `repo`, `main_sha`, optional `error` (at most 200 characters), optional `labels`, `findings` (critical and high counts and a link, or null) and per configured environment (`prod-plan` excluded) `live_sha`, `live_at`, `converged`, `pending_commits`, `pending_compare_url`, `gate`, `drift` and `health`. It SHALL carry counts, addresses and links only, never plan values, finding details or resource attributes. Renderers SHALL refuse any other `schema` value.

#### Scenario: Unknown schema version
- **WHEN** the site loads a snapshot whose `schema` is `ayasee.snapshot/v2`
- **THEN** it refuses to render it

### Requirement: Cell facts and their sources
The collector SHALL take the live SHA and convergence from the ledger's current record; compute pending commits by comparing the first environment's live SHA to `main` and each later one to its predecessor's live SHA; mark `gate.waiting` when a run of a listed caller workflow is `waiting` on a pending deployment for the (prefixed) environment; read the open `ayasee:drift` issue and its bullet count; derive `health.last_result` (`failed` if any unit failed, else `applied` if any applied, else `no_changes`, `unknown` without a record); and set `health.stop_the_line` from an open stop-the-line issue.

#### Scenario: Pending commits in uat
- **WHEN** `dev` is live at a SHA three commits ahead of `uat`'s live SHA
- **THEN** the `uat` cell has `pending_commits: 3`

### Requirement: Degraded data is null, not an error
A failure to read one fact SHALL make only that field null (a 403 on pending deployments nulls `gate.waiting`, a 404 or 422 on compare nulls `pending_commits`); a repository that fails entirely SHALL be listed with `error`; the collector SHALL exit 1 and commit nothing only when every repository failed.

#### Scenario: One repository unreachable
- **WHEN** one of five repositories returns errors for every call
- **THEN** the snapshot lists it with `error` and the other four normally

### Requirement: Compliance labels
The collector SHALL read each repository's configuration at `main` and set `labels` to the opt-ins that weaken a compliant default (`{non-compliant, solo_mode}`, `{deviation, identity}`, and the workflow-gate deviation), empty when none or when the file is missing, absent when the file cannot be read or is invalid.

#### Scenario: solo_mode repository
- **WHEN** a repository's `.ayasee.yaml` at `main` sets `solo_mode: true`
- **THEN** its row carries the `non-compliant` `solo_mode` label

### Requirement: Status precedence
Renderers SHALL reduce a cell to one status by the first matching rule: `held` (stop-the-line), `failed`, `drift`, `waiting`, `pending` (pending commits above 0), `synced` (`applied` or `no_changes` with a live SHA), otherwise `unknown`, as fixed by `schema/fixtures/status-precedence.json`.

#### Scenario: Drift while waiting
- **WHEN** a cell has open drift and a waiting gate
- **THEN** its status is `drift`

### Requirement: Column order
Columns SHALL be the ordered merge of each repository's stage sequence, inserting an unseen environment after its predecessor, with the first repository winning on conflicts (`schema/fixtures/env-order.json`).

#### Scenario: Different stage lists
- **WHEN** one repository has `[dev, uat, prod]` and another `[test, prod]`
- **THEN** `test` is placed by its predecessor relation and `prod` stays last

### Requirement: Change-driven history
The workflow SHALL commit `snapshot.json` and `history/YYYY/MM/DD/HHMM.json` to the data repository only when the snapshot changed apart from `generated_at`, and SHALL generate `history/index.json` for Pages on each run without committing it, listing the snapshots of the last `history-days` days (1 to 365, default 30); the site SHALL load exactly the listed snapshots (at most the newest 500) and compute the median dev-to-prod promotion lead time per repository.

#### Scenario: Quiet estate
- **WHEN** nothing changed since the last run except `generated_at`
- **THEN** no commit is made

### Requirement: Separate scoped tokens and ledger access
The workflow SHALL mint a read token limited to the repositories in `repos.yaml` plus those named in `github-branch://` ledger URLs (one owner, at most 500), a write token limited to the data repository and given only to the data checkout and commit step, and an issue token only for the `issue` renderer. The collector SHALL read `github-branch://` ledgers through the read token and `s3://` ledgers with its own read-only key (only the collect step receives it), SHALL refuse any other store URL before minting tokens, and SHALL never write a ledger.

#### Scenario: azure-blob ledger listed
- **WHEN** `repos.yaml` lists an `azure-blob://` ledger
- **THEN** the run fails before any token is minted

### Requirement: Pluggable renderers
The `renderer` input SHALL select `pages` (the static site deployed to Pages with `snapshot.json` and the indexed history), `issue` (a Markdown table in one data-repository issue) or `summary` (the same table in the job summary). The Markdown renderer SHALL emit `<status> <short sha> (+N)` per cell, escape repository and environment names, emit only https links, stay under 65,536 characters, and refuse to overwrite an issue whose body is non-empty and does not start with `<!-- ayasee:cockpit -->`.

#### Scenario: Foreign issue
- **WHEN** `render-md --issue-number` targets an issue with unrelated content
- **THEN** it refuses to overwrite it

### Requirement: Safe static site
The site SHALL validate the snapshot structurally, render every string as text, create only links that pass an https-only sanitizer (showing "link unavailable" otherwise), and open an accessible detail drawer (focus trapped, Esc closes, focus returns to the cell) containing deep links only.

#### Scenario: javascript URL in snapshot
- **WHEN** a snapshot field holds a `javascript:` URL
- **THEN** the site shows "link unavailable" instead of a link

### Requirement: Read-only by design
The cockpit SHALL offer no write action; approvals, syncs and re-drives SHALL stay in GitHub's UI behind deep links.

#### Scenario: Waiting gate
- **WHEN** a cell shows `waiting`
- **THEN** it links to the waiting run in GitHub and offers no approve button
