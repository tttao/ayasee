# The ledger

The ledger answers one question: **what is live in each environment of each repository?** It is the
source of truth for that (D5). The guard reads it to decide whether a stage may run, the nightly
drift check reads it to find the SHA to plan, and the cockpit reads it to show the live SHA.

The code is `src/ayasee/ledger.py`. Records carry unit names, statuses and booleans only, never
plan values.

## Why not the Deployments API

GitHub Deployments is convenient but weak as a source of truth. Any token with `repo` or
`repo_deployment` scope can create, inactivate or delete deployment records, and GitHub documents no
retention or immutability for them. A deployment record shows that a job referencing the environment
ran, not that the state converged, and it cannot say that one unit of three failed. The ledger is
written by the stage's own identity to the backend, next to the state it describes. Deployments is
written **as a projection** for the GitHub UI, see
[Deployments as a projection](#deployments-as-a-projection).

## The record

One record is written when a stage finishes:

| Field | Meaning |
|---|---|
| `repo`, `env`, `sha` | Repository (`owner/name`), environment, the commit that was applied |
| `run_id`, `run_attempt` | The workflow run that wrote it |
| `units` | Per unit: `status` (`applied`, `no_changes`, `failed`, `skipped`) and `converged` (the post-apply re-plan was empty) |
| `converged` | True only if every unit converged |
| `approver` | The distinct approved logins of the environment gate, sorted and comma-joined, or null (R36) |
| `breakglass_incident` | The incident reference of a break-glass deployment, or null |
| `recorded_at` | RFC 3339 UTC with microseconds |

A failed apply is still written (the units that did apply are recorded) and then the job fails.
An empty prod plan is recorded too: `prod-in-sync` writes every unit as `no_changes` and converged
at the new SHA, so drift checks never keep planning an older SHA. Empty `units` are refused.

## Stores and URLs

`ledger.url` in `.ayasee.yaml`, or the environment variable `AYASEE_LEDGER_URL`, selects the
store. The variable on a GitHub environment wins over the file; the run fails if neither is set.

| URL | Store | Access | Use it for |
|---|---|---|---|
| `azure-blob://<account>.blob.core.windows.net/<container>?prefix=<prefix>` | Blob container | Read and write; `DefaultAzureCredential` (OIDC in CI); needs the `ayasee[azure]` extra | The default for Azure adopters. Blobs carry the index tag `ayasee-sha`. |
| `git-branch:///abs/path/to/repo?branch=ayasee-ledger&remote=origin` | Orphan branch of a git repository | Read and write | Adopters without a storage account. A CI run that writes it needs a token with `contents: write` (the optional `ledger-token` secret, R32); the staged and nightly runs warn unless `main` is behind a ruleset that token cannot bypass ([details](staged-run.md#the-ledger-token-and-main)). |
| `github-branch://<owner>/<repo>?branch=ayasee-ledger` | The same branch, read through the contents API | **Read only** | The cockpit. `append` always raises. |
| `file:///abs/path` | A directory | Read and write | Tests and local trials. |

Defaults: `prefix` is `ayasee-ledger`; `branch` is `ayasee-ledger`.

### The git-branch store

The branch is written with git plumbing and a temporary index, so the caller's working tree, index
and `HEAD` are never touched. With `remote` set, each write does fetch, commit, push, and retries up
to 5 times when the push is rejected because someone else wrote first. It creates
`refs/heads/<branch>` and `refs/ayasee/remote/*` and refuses to run if the branch is checked out.
Commits are authored by `ayasee <ayasee@users.noreply.github.com>`.

### The github-branch store (read-only)

`github-branch://` exists so the collector can read the ledgers of many repositories without cloning
each. It lists `history/` and reads files through the contents API. Writers use `git-branch://`
against the same branch. A cockpit `repos.yaml` entry points at it, for example
`github-branch://acme/payments?branch=ayasee-ledger`. The collector's read token must be able to
read the contents of every repository named in such a URL, see [cockpit setup](../cockpit/setup.md).

## Layout

All stores use the same layout (`{prefix}` is `ayasee-ledger` unless an Azure URL overrides it):

```
{prefix}/{owner}/{name}/{env}/current.json
{prefix}/{owner}/{name}/{env}/history/{YYYYMMDDTHHMMSSffffffZ}-{sha[:12]}.json
```

History file names are fixed-width and sort lexicographically, so listing them gives the newest
first. Repository and environment names are validated as path segments (`..` is rejected).

A write appends the history entry **first**, then overwrites `current.json`. Writes are idempotent:
retrying the same record re-sends identical bytes and succeeds, while a history entry that already
exists with different content is an error.

## R19: the newest-of rule

`current()` does not trust `current.json` alone. It returns the **newer of `current.json` and the
newest history entry**, compared by `recorded_at`. Two things follow:

- A crash between the two writes (history written, `current.json` not) is healed on the next read,
  because the history entry is newer.
- `current.json` is kept monotonic. `append` only replaces it if the existing one is not strictly
  newer, so a late retry of an older run cannot move "live" backwards.

The cost is one extra directory listing per read.

## Who writes and who reads

| Actor | Access |
|---|---|
| `dev`, `uat` jobs | Write their own environment's record |
| `prod` job | Writes the `prod` record (with the approver) |
| `prod-in-sync` (runs in `prod-plan`) | Writes the `prod` record for an empty plan. The `prod-plan` identity therefore needs write on the prod record, but it cannot apply (R31). |
| `iac-breakglass` | Writes the `prod` record with the incident reference |
| `guard` | Reads the predecessor stage's record |
| `iac-nightly` (`drift-<env>`) | Reads the live SHA |
| Cockpit collector | Reads every record, read-only |

The guard rules, in order:

1. The predecessor of `prod` and `prod-plan` is `uat` (`prod-plan` writes no record and is skipped
   when looking for a predecessor). If there is no recordable predecessor, the guard says
   `predecessor_missing` (R22).
2. A predecessor whose record is at a different SHA is `superseded` if it converged, otherwise
   `predecessor_missing`. A record at this SHA that did not converge is `predecessor_not_converged`.
3. For `prod-plan` and `prod`, an open `ayasee:stop-the-line` issue for any environment up to and
   including prod is `stop_the_line`.

Any infrastructure error fails closed with exit 1.

## Deployments as a projection

With `--mirror-deployments` (the `mirror-deployments` input of the ledger action), the ledger write
also creates a GitHub deployment and a status:

- `ref` is the **commit SHA**, not `main`. A branch name would resolve to the branch tip, which may
  be newer than what was applied. `payload` records the SHA and `branch = "main"`.
- `production_environment` is true for `prod`; the status is `success` or `failure` by convergence,
  with a log URL to the run.
- The mirror is refused unless `GITHUB_REF` is `refs/heads/main` (D5), so only runs from `main`
  produce records. PR plan jobs run in the separate `dev-plan` environment so they do not pollute the
  `dev` history.
- If the mirror fails after the ledger was written, the command reports "ledger written, but
  deployment mirror failed". The ledger is already correct.

Treat Deployments as an index for humans. Nothing in ayasee reads it as authority.

## Inspect it

```bash
ayasee ledger show --store "$AYASEE_LEDGER_URL" --repo acme/payments --env prod
ayasee ledger show --store "$AYASEE_LEDGER_URL" --repo acme/payments --env prod --history 10
```
