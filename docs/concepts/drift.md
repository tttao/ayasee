# Drift and re-scan

`iac-nightly` is a reusable workflow. The calling repository adds the schedule; the workflow does
two independent things: it checks every environment for drift, and it re-scans `main`.

```yaml
# .github/workflows/iac-nightly.yml in the adopting repository
on:
  schedule:
    - cron: "17 2 * * *"   # nightly, on the default branch
jobs:
  nightly:
    uses: <owner>/ayasee/.github/workflows/iac-nightly.yml@<sha>
    with:
      config-path: .ayasee.yaml
    secrets:
      teams-webhook: ${{ secrets.TEAMS_WEBHOOK }}   # optional
      ledger-token: ${{ secrets.LEDGER_TOKEN }}     # optional; git-branch:// ledger store only
```

## Drift check

The `resolve` job reads the configured `stages` (without `prod-plan`) and the `drift` job runs once
per environment.

1. Read the ledger record of the environment. No record means nothing is live yet: a notice, and
   the environment is skipped.
2. If an open issue labelled `ayasee:apply-failed` and `env:<env>` exists, skip the environment.
   The state may be half-applied, so a plan would report noise.
3. Check out the SHA the ledger says is live (`fetch-depth: 0`), **not** `main`. Drift means "the
   cloud differs from what we last applied", so a newer merge that is not yet deployed must not show up.
4. Plan every unit in `drift` mode: `plan -detailed-exitcode` with the normal state lock. Exit 2 is
   not a failure, the exit code of every unit goes to `exit-codes.json`, and the plan files are
   deleted once the JSON exists (they are never applied, so they are not encrypted).
5. `ayasee drift evaluate` decides drift, `ayasee drift sync` keeps the issue.
6. Teams gets `drift_found` when a run newly opens an issue and `drift_cleared` when it closes one.
   Repeated nights comment on the open issue and do not notify again.

The plan waits for the state lock (`-lock-timeout`, input `lock-timeout`, default `10m`, format
`<n>s` or `<n>m`). Staged plans and applies use the same timeout, so a run that overlaps a nightly drift
plan waits for the lease instead of failing and opening a spurious stop-the-line issue. The plan
action also verifies (`expect-sha`) that the checkout really is the ledger SHA. If the drift job
fails, Teams gets `stage_failed` for the environment `drift-<env>`.

Exit code 1 or any other value is an error and fails the job. It is never read as "no drift".

### Environments and identities

Every drift job runs in the GitHub environment `drift-<env>` (`drift-dev`, `drift-uat`,
`drift-prod`): branch policy `main` only, no reviewers, so the nightly run never waits for a person.
Each needs the usual variables (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
`AYASEE_LEDGER_URL`, optional) and an Entra federated credential for
`repo:<owner>/<repo>:environment:drift-<env>`.

The identity is a plan identity: Reader on the subscription and Storage Blob Data Contributor on that
environment's state container (it needs write to take the state lease), plus read on the ledger. It
has no apply rights and must not be the identity the stage applies with. Prod drift therefore cannot
approve or change anything.

The job holds `id-token: write`, `contents: read`, `issues: write`, `actions: read`, and runs in its own
concurrency group `ayasee-drift-<repo>-<env>` with `cancel-in-progress: false`, separate from the
apply groups.

### What counts as drift

Per unit, only managed resources with a real change count. Data sources, no-op and read are ignored.

- Create, delete, replace, import, forget (and a pending move) are always drift.
- An update is **noise**, and ignored, when every attribute path that differs between `before` and
  `after` falls under a configured noise path for the resource type. A path is dotted and list
  indexes are numbers, so the noise path `access_control` covers `access_control.0.permission_level`.
  An update whose paths cannot be determined is drift.
- A changed root output (created, updated or deleted) is drift too, reported as `output.<name>`
  (R72): the live state no longer gives what the code at the live SHA declares. Output values are
  never read.

```yaml
# .ayasee.yaml
drift_noise:
  databricks_permissions:
    - access_control
```

Noise is read from the configuration at the live SHA.

### The issue

One issue per environment: title `drift: <env>`, labels `ayasee:drift` and `env:<env>`. The body
lists the drifted addresses (as `<unit>: <address>`, never values) and the run link. A later night
with drift adds a comment. A night with no drift comments `drift cleared at <sha>` and closes it.

### Prod drift holds prod

For `prod` the issue also carries `ayasee:stop-the-line`. The guard refuses to start `prod-plan` and
`prod` while an open stop-the-line issue exists for an environment up to and including prod, so prod
drift holds the next prod rollout.

To clear it, either fix the cause and let the next nightly run find no drift (it closes the issue),
or remove the drift deliberately: apply the missing change through the normal staged run, or add a
noise path if the difference is harmless. Closing the issue by hand also releases the hold, but the
next night opens it again if the drift is still there. Apply failures use the same mechanism and
carry `ayasee:apply-failed`; they are cleared by closing their issue after the repair.

## Re-scan

The `rescan` job runs Trivy (config) and Checkov (Terraform HCL and workflows) on `main`, scrubs
the SARIF (`sarif-scrub`) and uploads it with the categories `ayasee-trivy` and `ayasee-checkov`,
the same ones the pull-request checks use. Findings do not fail the run.

It holds `security-events: write` in the same job as the scanners. This is deliberate: it only
scans `main`, which is reviewed and merged code. Pull-request code never reaches it (see
`pr-checks.md` for the split used there).

### The new-findings window

After the upload, `ayasee findings new --repo <repo> --since-hours 26` counts the open code-scanning
alerts of severity high or critical that were created in the last 26 hours. The window is two hours
longer than the schedule so that a late run or a clock skew cannot lose an alert. When the count is
above zero, Teams gets `new_findings`. The card carries counts and links only, never finding details.
