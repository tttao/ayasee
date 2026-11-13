# Cockpit setup

The cockpit collects one `snapshot.json` for a whole estate of repositories that use ayasee, keeps a dated history, and renders it as a site, an issue or a job summary. The snapshot holds counts, addresses and links only: never plan values, never finding details.

```
repos.yaml ──> cockpit-collect (reusable workflow) ──> cockpit-data repo: snapshot.json + history/
                                                    └─> renderer: pages | issue | summary
```

## 1. Create and install the GitHub App

Create one GitHub App (organization settings, Developer settings, GitHub Apps). Repository permissions, all you need:

| Permission | Level | Used for |
|---|---|---|
| Metadata | read | repository and branch lookup |
| Actions | read | runs waiting on a gate |
| Issues | read | drift and stop-the-line issues |
| Contents | read | ledger (`github-branch://` stores), compare, the repository's `.ayasee.yaml` (labels) |
| Deployments | read | pending deployments of waiting runs |
| Code scanning alerts (security events) | read | finding counts |
| Contents (data repo only) | write | commit `snapshot.json` and `history/` |
| Issues (data repo only) | write | only for the `issue` renderer |

**Install the App on selected repositories only**: the repositories in `repos.yaml`, the repositories named in `github-branch://` ledger URLs, and the data repository. Do not choose "all repositories". The workflow mints its tokens from the App per run (`actions/create-github-app-token`):

- a **read token** limited to the repositories listed in `repos.yaml` (the `repo:` entries plus any repository named in a `github-branch://` ledger URL; at most 500, all of the data repository's owner, otherwise the run fails), read permissions only, used by the collector. It can read: the contents (code, branches, the ledger) and the code-scanning alert details of those repositories, plus their metadata, workflow runs, deployments and issues. It cannot write anything and cannot see any other repository;
- a **write token** scoped to the single data repository with `contents: write`, used to commit;
- for the `issue` renderer only, an **issue token** scoped to the data repository with `issues: write` and nothing else.

The estate repositories and the data repository must share one owner. Store the App ID and private key as the secrets `COCKPIT_APP_ID` and `COCKPIT_APP_PRIVATE_KEY` of the wrapper repository.

### Why two tokens

The collector and the writer have opposite needs, so they get separate tokens minted from the same App:

- Collection must read every repository of the estate, which is a wide read surface, so the **read token** is read-only and limited to the listed repositories. Whatever runs with it cannot change anything, and a leak exposes read access to those repositories only.
- Writing must only ever touch the data repository. The **write token** is scoped to that single repository, so a bug or a compromised step cannot push to an estate repository, even though the App is installed on all of them. It is given to the data checkout and the commit step only, and the issue renderer gets its own `issues: write` token, so no step holds more write access than it uses.
- Keeping them apart means no token is both wide and writable. The collector step receives the read token and no write access; the commit step receives the write token and no estate read access.

## 2. The data repository

Create an empty repository, for example `acme/cockpit-data`. The workflow creates and maintains:

```
repos.yaml                     you maintain this (see below)
snapshot.json                  latest snapshot
history/YYYY/MM/DD/HHMM.json   one copy per change, UTC
```

**Create and commit `repos.yaml` before the first run**: the workflow reads it from the data repository and fails without it.

`history/index.json` (the list of snapshots inside the window that the site reads for its history view and lead time) is **generated for Pages on each run and never committed**: it depends on the window and on the run time, so committing it would add a commit per run to an otherwise change-driven history. The window is the `history-days` input (integer, 1 to 365, default 30). The index window is authoritative: the site shows and computes lead times over exactly the snapshots the index lists (at most the newest 500), with no further day filter. Only `history/index.json` and the snapshot files it lists are published to Pages. The site always gets the snapshot of the current run, so a quiet estate is not shown as stale; the committed `snapshot.json` keeps the time of the last change.

A snapshot is committed only when it changed apart from `generated_at`; an unchanged estate produces no commits. Commits are authored by `ayasee-cockpit[bot]`.

### repos.yaml

A YAML list of `{repo, ledger, stages?, workflows?, config?}`. `config` (default `.ayasee.yaml`) is the repository's ayasee configuration, read at `main` for the cockpit's [labels](../concepts/cockpit.md#the-snapshot-contract-v1).

**`cockpit-collect` reads `github-branch://` and `s3://` ledgers.** It reads a `github-branch://` ledger through the read token (the contents API). A repository whose ayasee ledger is a `git-branch://` store is listed with the matching `github-branch://owner/repo?branch=<branch>` URL. An `s3://` ledger (experimental) is read with the cockpit's own read-only key, see [s3 ledgers](#s3-ledgers). A ledger in Azure Blob storage (`azure-blob://`), or any other store URL, is refused by `ayasee cockpit repos` and the run fails before any token is minted. The collector never writes a ledger, whatever the store: it only ever holds a read-only view of it.

```yaml
- repo: acme/payments
  ledger: github-branch://acme/payments?branch=ayasee-ledger
- repo: acme/platform
  ledger: github-branch://acme/platform?branch=ayasee-ledger
  stages: [dev, uat, prod]
  workflows: [.github/workflows/main.yml]   # callers of iac-staged; their waiting runs count as gates
- repo: acme/edge
  ledger: s3://acme-ledger?region=auto&endpoint=https://<account>.r2.cloudflarestorage.com
```

The format, defaults and error handling are described in the docstring of `src/ayasee/cockpit/collect.py`.

### s3 ledgers

**Experimental**, like [the s3 store](../concepts/ledger.md#the-s3-store) itself: tested against an in-process fake S3 only, not yet against a real S3 or R2 bucket.

- **The URL** is the one the repository uses (`s3://<bucket>?region=<region>&prefix=<prefix>&endpoint=https://<host>`), with `region` always in the URL: the workflow sets no `AWS_REGION`, and `ayasee cockpit repos` refuses an `s3://` ledger without it, or one that the store would refuse, before any token is minted. An `s3://` ledger adds no repository to the read token.
- **The key** is the cockpit's own, read-only, held as two optional secrets of the wrapper repository and passed by name: `AYASEE_COCKPIT_AWS_ACCESS_KEY_ID` and `AYASEE_COCKPIT_AWS_SECRET_ACCESS_KEY`. Only the "Collect the snapshot" step receives them, as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`; no step that holds the write or issue token does. One pair serves every `s3://` ledger of the estate, so ledgers in several accounts need one key that can read all of them. Never reuse a ledger writer's key here: give it only

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      { "Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::<bucket>/<prefix>/*" },
      {
        "Effect": "Allow",
        "Action": "s3:ListBucket",
        "Resource": "arn:aws:s3:::<bucket>",
        "Condition": { "StringLike": { "s3:prefix": "<prefix>/*" } }
      }
    ]
  }
  ```

  (on Cloudflare R2, an API token with "Object Read only" on the ledger bucket).
- **The endpoint** comes from `repos.yaml`, which lives in the data repository: whoever can change that file chooses where the cockpit's signed read requests go. Restrict who may push to the data repository accordingly; the key it exposes can only read the ledgers.
- **Errors**: a missing key, a refused request or an unreachable store marks that repository with an error naming the variable, or the HTTP status and S3 error code, never a response body or a credential. Each environment costs one listing and up to two GETs, and no GitHub API request.

```yaml
    secrets:
      app-id: ${{ secrets.COCKPIT_APP_ID }}
      app-private-key: ${{ secrets.COCKPIT_APP_PRIVATE_KEY }}
      AYASEE_COCKPIT_AWS_ACCESS_KEY_ID: ${{ secrets.AYASEE_COCKPIT_AWS_ACCESS_KEY_ID }}       # s3:// ledgers only
      AYASEE_COCKPIT_AWS_SECRET_ACCESS_KEY: ${{ secrets.AYASEE_COCKPIT_AWS_SECRET_ACCESS_KEY }}
```

## 3. The wrapper workflow

`cockpit-collect` is a reusable workflow. A wrapper adds the triggers, which a reusable workflow cannot own. Do not give the wrapper a `concurrency` group named `ayasee-cockpit`: the called workflow already uses it: one collector runs at a time and none is cancelled while running, but a newer pending run replaces an older pending one, so at most one run waits. The group is per repository, so use **one wrapper repository per data repository**: two wrappers would collect and push to the same data repository concurrently.

```yaml
name: cockpit
on:
  workflow_run:
    workflows: [main]            # the callers of iac-staged / iac-nightly in THIS repository
    types: [completed]
  schedule:
    - cron: "*/30 * * * *"       # fallback, and the only trigger for other repositories
  workflow_dispatch:

permissions: {}

jobs:
  cockpit:
    permissions:
      contents: read     # required: the called workflow's collect job needs it
      pages: write       # pages renderer only; the called workflow's pages job needs these
      id-token: write
    uses: OWNER/ayasee/.github/workflows/cockpit-collect.yml@<release-sha>
    with:
      renderer: pages            # pages | issue | summary
      data-repository: acme/cockpit-data
      repos-file: repos.yaml
      # issue-number: "1"        # issue renderer only
      # history-days: "30"       # pages renderer only, 1 to 365
    secrets:
      app-id: ${{ secrets.COCKPIT_APP_ID }}
      app-private-key: ${{ secrets.COCKPIT_APP_PRIVATE_KEY }}
```

`workflow_run` only fires for workflows of the same repository, so events from other repositories are picked up by the 30-minute schedule. Pin the reusable workflow to a released commit SHA. Drop `pages` and `id-token` from the wrapper permissions for the `issue` and `summary` renderers; keep `contents: read` in every case.

### Renderers

| `renderer` | Result | Needs |
|---|---|---|
| `pages` | static site with the data, deployed to GitHub Pages of the **wrapper** repository (built by the `actions/cockpit-site` action; `snapshot.json` and `history/` sit at the site root) | Pages enabled (source: GitHub Actions) in the wrapper repository |
| `issue` | Markdown dashboard written into issue `issue-number` of the data repository (`ayasee cockpit render-md --issue-number N --repo OWNER/NAME`) | an issue, see below |
| `summary` | Markdown dashboard appended to the job summary (`render-md --summary "$GITHUB_STEP_SUMMARY"`) | nothing |

**Issue marker.** The renderer replaces the whole issue body, so it refuses to touch a human issue: the body must be empty, or start with `<!-- ayasee:cockpit -->`. Create the issue empty (or with just that marker line); the renderer keeps the marker in every body it writes.

## 4. Visibility (D8)

The snapshot reveals the estate shape (repositories, environments, commit addresses, links to runs and issues), so decide who may see it.

- **Private Pages only on GHEC (github.com Enterprise Cloud):** access control restricts the site to people who can read the repository. Use `pages` there with access control set to private. Without it (a public github.com plan), the site is public.
- **GHES: Pages is instance-wide.** The site is visible to every signed-in user of the instance (or public), whatever the repository permissions say. Use the `issue` or `summary` renderer (visibility follows the data repository or the run), or accept that the published data is sanitized. The snapshot contains counts, addresses and links only, so it is sanitized by construction, but the repository list itself may still be sensitive.
- The `summary` renderer and the 1-day `cockpit-data` workflow artifact (the data handed to the Pages job) follow the visibility of the **wrapper** repository, not of the data repository: anyone who can read the wrapper's runs can read them. Keep the wrapper repository as private as the data repository.
- **Private Pages cannot be detected.** The API does not reveal Pages visibility, and GHEC cannot be told apart from github.com. `ayasee features` reports `private_pages: false` unless you state otherwise with `--private-pages true`. Decide from your plan and set the override; do not rely on detection.
- Keep the data repository private either way; the `issue` and `summary` renderers inherit its visibility.

## 5. Rate-limit cost

Per repository and run, about `6 + 7 x environments` API requests (`6 + 4 x environments` with an `s3://` ledger, whose reads go to the store) (default three environments: about 27). A hundred repositories is about 2,700 requests per run, 130,000 per day at the 30-minute cadence, spread over the App installation's budget (it scales with the installation size, starting at 5,000 per hour; GHES and enterprise limits differ). The collector sends conditional requests (ETags) and the workflow keeps the cache file `.ayasee/etag-cache.json` in `actions/cache` between runs, so unchanged responses come back 304 and do not count against the primary limit. The cache holds API JSON only (never credentials), at most 2,000 entries and about 20 MB. A repository that fails to collect is still listed with an error; the job fails only when every repository failed, and then nothing is committed.
