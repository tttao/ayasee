# The cockpit

The cockpit is a read-only view of an estate: **repositories as rows, environments as columns**. Each
cell says what is live, what is waiting to be promoted, whether a gate is waiting for a person, whether
there is drift, and whether the environment is healthy. It has no server: a workflow collects one
JSON snapshot, commits it to git, and a renderer presents it.

It is "Argo CD-style visibility", not "Argo CD for Terraform". It never approves, syncs or
re-drives; every action is an https deep link into GitHub's own Actions, issues and code-scanning
pages. For setup, see [cockpit setup](../cockpit/setup.md). For where it sits, see the
[architecture](architecture.md#observation-plane).

The code is `src/ayasee/cockpit/` (collector, Markdown renderer, history index),
`cockpit-site/` (the site) and `schema/snapshot.v1.json` (the contract).

## The snapshot contract (v1)

The snapshot is the product's public contract. One schema, many renderers (D8): the same file feeds
the static site, a pinned issue and a job summary. Its schema id is `ayasee.snapshot/v1` and the
JSON Schema is [`schema/snapshot.v1.json`](../../schema/snapshot.v1.json). A site or renderer that
sees another `schema` value refuses it.

**It carries counts, addresses and links only.** No plan values, no finding details, no resource
attributes.

```json
{
  "schema": "ayasee.snapshot/v1",
  "generated_at": "2026-11-06T09:30:00Z",
  "repos": [
    {
      "repo": "acme/payments",
      "main_sha": "9f3c1a2...",
      "findings": { "critical": 0, "high": 2, "url": "https://github.com/acme/payments/security/code-scanning" },
      "envs": [
        {
          "env": "prod",
          "live_sha": "7be40d1...",
          "live_at": "2026-11-05T16:02:11.482113Z",
          "converged": true,
          "pending_commits": 3,
          "pending_compare_url": "https://github.com/acme/payments/compare/7be40d1...9f3c1a2",
          "gate": { "waiting": true, "since": "2026-11-06T08:10:00Z", "run_url": "https://github.com/acme/payments/actions/runs/1" },
          "drift": { "open": false, "since": null, "issue_url": null, "resources": 0 },
          "health": { "last_result": "applied", "stop_the_line": false }
        }
      ]
    }
  ]
}
```

| Field | Meaning | Source |
|---|---|---|
| `repos[].repo`, `main_sha` | Repository and the head of its default branch | GitHub API |
| `repos[].error` | Present when the repository could not be collected (at most 200 characters); its environments are unknown | Collector |
| `repos[].labels` | Optional. The opt-ins of the repository's `.ayasee.yaml` at `main` that weaken a compliant default, each `{kind, setting}`: `{non-compliant, solo_mode}` (no independent approval, D2) and `{deviation, identity}` (`identity: secrets`, stored provider tokens instead of OIDC, D7). Empty when none or when the file is missing; absent when unknown (the file cannot be read or is invalid). The site shows each as a tag under the repository name, the Markdown view as a "Labels" list. | Contents API (`config` in `repos.yaml`, default `.ayasee.yaml`) |
| `repos[].findings` | Open code-scanning alerts of severity critical and high, and a link. Repo level, because scans run on `main` and on pull requests, not per environment (R54). Null when unavailable. | Code-scanning API |
| `envs[]` | The repository's configured stages, `prod-plan` excluded | `repos.yaml` `stages` (default `dev, uat, prod`) |
| `live_sha`, `live_at`, `converged` | The ledger's current record | [Ledger](ledger.md) |
| `pending_commits`, `pending_compare_url` | The first environment compares its live SHA to `main`; each later one to its predecessor's live SHA. Null when unknown (no live SHA, or a compare that returned 404 or 422). | Compare API |
| `gate.waiting`, `since`, `run_url` | A run of a listed caller workflow with status `waiting` whose pending deployments include this environment; `since` is the run's creation time. Null `waiting` when the token cannot read pending deployments. | Actions API |
| `drift.open`, `since`, `issue_url`, `resources` | The open `ayasee:drift` issue for the environment and the number of drifted-resource bullet lines in it | Issues API |
| `health.last_result` | `failed` if any unit failed, else `applied` if any applied, else `no_changes`; `unknown` without a record | Ledger |
| `health.stop_the_line` | An open `ayasee:stop-the-line` issue exists for the environment | Issues API |

A degraded cell is `null`, not an error: a 403 on pending deployments makes only `gate.waiting`
null; a 404 or 422 on compare makes only `pending_commits` null. A repository that fails entirely is
still listed with `error`. The collector exits 1 only when every repository failed, and then nothing is
committed.

### How a cell becomes a status

Renderers reduce a cell to one status. The first rule that matches wins, otherwise `unknown`
(`schema/fixtures/status-precedence.json`, shared by the site and the Markdown renderer):

| Order | Status | When |
|---|---|---|
| 1 | `held` | `health.stop_the_line` is true |
| 2 | `failed` | `health.last_result` is `failed` |
| 3 | `drift` | `drift.open` is true |
| 4 | `waiting` | `gate.waiting` is true |
| 5 | `pending` | `pending_commits` is above 0 |
| 6 | `synced` | `last_result` is `applied` or `no_changes` and `live_sha` is set |

### Column order

Repositories can have different stage lists. Columns are the ordered merge of each repository's
sequence: an unseen environment is inserted after its predecessor, and the first repository wins on
conflicts (R59, `schema/fixtures/env-order.json`).

## Renderers

The renderer is chosen with the `renderer` input of `cockpit-collect`. Hosting is pluggable because
the audience that most needs no SaaS (GHES, regulated, air-gapped) is the one with the hardest Pages
access model.

| `renderer` | Output | Choose it when |
|---|---|---|
| `pages` | The static site (`cockpit-site/`, built by `actions/cockpit-site`) deployed to GitHub Pages of the wrapper repository, with `snapshot.json` and `history/` at its root | Pages access can be restricted to people who may read the repository |
| `issue` | A Markdown table written into one issue of the data repository (`ayasee cockpit render-md --issue-number N --repo OWNER/NAME`) | You want visibility to follow repository permissions |
| `summary` | The same table appended to the job summary (`render-md --summary "$GITHUB_STEP_SUMMARY"`) | Nothing else: it needs no setup |

The Markdown renderer emits `<status> <short sha> (+N)` per cell, escapes repository and environment
names so they cannot inject markup, emits only https links, and keeps the issue body under GitHub's
65,536-character limit. The issue renderer refuses to overwrite an issue whose body is not empty and
does not start with `<!-- ayasee:cockpit -->`.

The site is Vite and TypeScript with no framework. It validates the snapshot structurally, renders
every string as text, and only creates links that pass an https-only sanitizer; an unsafe URL shows
"link unavailable". Clicking a cell opens a detail drawer (live SHA and age, pending commits with a
compare link, gate state, drift, health) with deep links only. Focus is trapped in the drawer, Esc
closes it and focus returns to the cell.

## History

Each time the snapshot changes, the workflow commits a dated copy to the data repository:

```
snapshot.json                  latest snapshot
history/YYYY/MM/DD/HHMM.json   one copy per change, UTC
```

A snapshot is committed only when it changed apart from `generated_at`, so a quiet estate produces no
commits, and history and trends come from `git log` for free. `history/index.json` is **generated for
Pages on each run and never committed**, because it depends on the window and on the run time. It
lists the snapshots of the last `history-days` days (1 to 365, default 30).

That window is authoritative (R61): the site loads exactly the snapshots the index lists, at most the
newest 500, with no day filter of its own. It uses them for the history view and for the **promotion
lead time**: for each commit that was live in both `dev` and `prod`, the hours from first live in
`dev` to first live in `prod`, reported as a median per repository. Only `history/index.json` and the
files it lists are published to Pages.

## Visibility (D8)

A static site cannot authorise each viewer. A snapshot of an estate reveals its shape: which
repositories exist, which environments, which commits are waiting, where there are findings. That is
why the contract is counts, addresses and links only, and why the permission checks stay with GitHub:
a link to code scanning or a run only opens for someone who may see it.

| Host | What to do |
|---|---|
| github.com Enterprise Cloud | Private Pages: access follows read access to the publishing (wrapper) repository. Use the `pages` renderer with access control set to private. Without it the site is public. |
| GitHub Enterprise Server | Pages is instance-wide. Use the `issue` or `summary` renderer, whose visibility follows the data repository or the run. The GHES Pages documentation found for this is old and needs re-checking; this behaviour is unverified here. |
| Anywhere | Keep the data repository and the wrapper repository private. The `summary` renderer and the 1-day `cockpit-data` artifact follow the wrapper repository's visibility, not the data repository's. |

Pages changes can take up to ten minutes to appear, so the cockpit is a near-real-time report. Teams
cards stay the alerting channel for "prod is waiting".

Tokens are separate and scoped (R56, R62): a read token limited to the repositories in `repos.yaml`
plus the repositories named in `github-branch://` ledger URLs (all of one owner, at most 500), a write
token for the single data repository, and an issue token for the `issue` renderer only. See
[why two tokens](../cockpit/setup.md#why-two-tokens).
