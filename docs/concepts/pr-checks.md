# Pull-request checks

`iac-pr` is a reusable workflow for pull requests. It runs static checks and scans, plans the
changed units of the **first stage** (`dev` by default) with a read-only identity, runs the policy pack on that plan and posts one
sticky comment with the risk summary. **It never applies**: no apply, no guard, no ledger write, and
nothing the plan produced is uploaded. The only artifacts are the scrubbed SARIF and the comment
text, both kept for one day (the SARIF 30 days with [code scanning off](#code-scanning)). Running a job in the
[pull-request plan environment](#the-pull-request-plan-environment) still creates
a deployment record for that environment (separate from the first stage's, which is why the
environment has its own name).
Suppressing that record where the platform allows it needs feature detection, which belongs to the
GHES compatibility work that has not been done yet (see
[known limitations](architecture.md#known-limitations)).

```yaml
# .github/workflows/iac-pr.yml in the adopting repository
on:
  pull_request:          # never pull_request_target
jobs:
  iac:
    uses: <owner>/ayasee/.github/workflows/iac-pr.yml@<sha>
    # no secrets: the workflow needs none
```

The caller must use `pull_request`. `pull_request_target` runs with repository secrets and a write
token against code the author controls; the workflow's jobs also check `github.event_name ==
'pull_request'` and do nothing for any other event.

## Flow

No job that evaluates the PR's HCL (`terragrunt hcl validate`, plan) holds `pull-requests: write` or
`security-events: write`. PR code can run provider and module code in those jobs, so they hand their
result to small jobs that run no PR code (R39).

| Job | Permissions | What it does |
|---|---|---|
| `checks` | `contents: read` | `tofu fmt -check -recursive`, `terragrunt hcl format --check`, `terragrunt hcl validate`; Trivy `config` on `iac_root` (the repository by default; SARIF, no cache); Checkov via `uvx checkov@<version>` on `iac_root` (SARIF, with `--baseline .ayasee/checkov.baseline` when the file exists). Both scanners run even if an earlier step failed, so all SARIF is kept; the job still fails if any step failed. A scanner that crashes writes no SARIF: its scrub step then fails with `::error::Trivy wrote no SARIF` (or Checkov), never passes. Trivy is the version stated on the `trivy-action` steps (`v0.70.0`). Each SARIF goes through `ayasee sarif-scrub` and the scrubbed files are kept as artifact `ayasee-sarif` (1 day; 30 days with `code-scanning: false`, when its summary says code scanning is off and gives the count of each scanner). |
| `upload-sarif` | `security-events: write`, `actions: read`, `contents: read` | No checkout. Downloads `ayasee-sarif` and uploads it with `github/codeql-action/upload-sarif`, categories `ayasee-trivy` and `ayasee-checkov`. Skipped for fork PRs and when the caller passes `code-scanning: false` ([code scanning](#code-scanning)). |
| `resolve` | `contents: read` | No environment, no identity. Checks out the PR head and prints the environment `plan-first-stage` runs in with `ayasee config pr-plan-environment`: `<environment_prefix><first stage>-plan` (`dev-plan` by default, `test-plan` with `stages: [test, prod]`, `iac-test-plan` with `environment_prefix: iac-`). It fails when the configuration does not load, the first stage is `prod` or a stage name is reserved (`prod-pr`, `prod-breakglass`, `drift-*`, `*-plan`); `plan-first-stage` is then skipped, `plan-dev` fails and `comment` posts "the configuration was refused". |
| `plan-first-stage` | `contents: read`, `id-token: write` | Environment `<first stage>-plan`, from `resolve` (a job's environment is fixed before its steps run). Checks out the PR head (`persist-credentials: false`), finds the changed units of the first configured stage (`dev` by default), logs in to Azure when `identity` is `oidc` (with `identity: secrets` the plan step receives that environment's provider tokens instead, so keep them read-only: pull-request code runs with them), plans (`binding-mode: same-job`, `encrypt: false`, `lock: false`, `allow-no-units: true`; plan files never leave the runner), runs the Rego pack on the plan JSON (with that stage; not when the stage has no units) and writes the comment text, headed "Plan of `<stage>`", to artifact `ayasee-pr-comment` (1 day). |
| `plan-dev` | none | The verdict of the pull-request plan, and the check to require. No checkout, no environment, no identity. It runs whenever `resolve` ran, also after a failure, and fails unless `resolve` succeeded and `plan-first-stage` succeeded. A skipped job counts as passed in branch protection, so requiring `plan-first-stage` alone would let a refused configuration merge. |
| `shared-changes` | `contents: read` | Only when `shared_units` is set. No environment, no identity: works out which shared prod units the PR changes (`ayasee changed-units --env prod --shared ...`). With `gate_mechanism: workflow` it hands none to `plan-shared` and sets `pending` instead ([iac-pr-shared](#with-the-workflow-gate-iac-pr-shared)). |
| `plan-shared` | `contents: read`, `id-token: write` | Only when `shared-changes` found some. Environment `prod-pr-plan`. Plans those units against prod (`binding-mode: replan`, `encrypt: false`, `lock: false`; never applied, the plan files are deleted), runs the pack (stage `prod`) and keeps the comment text as `ayasee-pr-comment-shared`. See [shared units](#shared-units). |
| `comment-shared` | `contents: read`, `pull-requests: write` | Like `comment`, for a second sticky comment (`--marker pr-plan-shared`), so the first stage's comment never waits for the `prod-pr-plan` approval. When `pending`, a fixed text: which pull request and head SHA a prod reviewer authorises with `iac-pr-shared`. |
| `comment` | `contents: read`, `pull-requests: write` | No OIDC and no PR code: checks out only the **base** commit (`persist-credentials: false`; never the PR head, R39), which also gives the in-repo `--local` form its `./actions`. Installs ayasee from the action, downloads only `ayasee-pr-comment` (or, when `resolve` failed, writes a fixed "the configuration was refused" text) and runs `ayasee pr-comment`. `plan-first-stage` refuses settings that are not supported yet before its Azure login. |

### Required checks

Require `checks` and `plan-dev` (shown as `<caller job> / checks` and `<caller job> / plan-dev`).
Do not require `plan-first-stage` or `resolve` instead of `plan-dev`: `plan-first-stage` is skipped
when `resolve` refuses the configuration, and a skipped check passes. All three are skipped for
fork pull requests, which get no plan.

### Which units are planned

`ayasee changed-units --base <base sha> --head <head sha> --units-root stages --env dev` diffs
the PR against its base (merge base) and prints:

- a comma list of changed dev units, planned through the plan action's `only-units` input;
- an empty line when nothing changed, or when only documentation changed (`*.md`, `docs/**`,
  `LICENSE`): the plan is skipped and the comment says there are no IaC changes;
- `ALL` when any changed file that is not documentation is inside `iac_root` but outside `stages/dev/` (modules,
  `root.hcl`, `.ayasee.yaml`, other environments), when a changed file under `stages/dev/` belongs to no unit (for example a
  removed unit), or when the diff cannot be computed. Every dev unit is then planned. A change to
  the configuration file (`--config-path`) always gives `ALL`.

Files outside `iac_root` are ignored like documentation (see [monorepos](#monorepos)). With the
default `iac_root: .` nothing is outside it.

Dependents of a changed unit are not added to the list; they are planned only when `ALL` applies.

If change detection itself fails, the comment says the plan failed before it started; it never
claims there are no IaC changes.

A first stage whose units directory exists but holds no unit yet (issue #56: a pull request that
wires the callers, the bootstrap and `root.hcl` before the first unit, where `.ayasee.yaml` and
`iac-*.yml` give `ALL`) plans nothing, and that passes: `plan-first-stage` and `plan-dev` succeed,
the policy pack does not run, and the comment says "No units under `<units_root>/<stage>`: nothing
was planned." It names the directory only when its path is plain (letters, digits, `.`, `_`, `-`,
`/`), since the pull request writes `units_root`. When the base commit has units in that stage
(under the base's own `units_root`), the comment adds a warning: a pull request that deletes every
unit of the first stage, or points `units_root` at an empty directory, gets a green plan check, but
never a comment that hides it. A missing `<units_root>/<stage>` still fails the plan; `ayasee init`
writes a `.gitkeep` in each stage. See
[stages without units](staged-run.md#stages-without-units) for the staged run.

### The comment

One comment per PR, found by the hidden marker `<!-- ayasee:pr-plan -->` and updated in place.
Only a comment by the expected bot login (`--author`, default `github-actions[bot]`, type `Bot`) is
taken over. Its body is the change set summary the plan action renders: risk tiers, resource
addresses, principals gained or lost. It never carries resource values; backticks, line breaks and
HTML in addresses and principal names are neutralised. **The comment is advisory**: the PR's own code
produces the plan it summarises, so an author can influence what it says. Decide on the plan and the
required checks, not on the comment alone.

## The pull-request plan environment

The pull-request plan environment is named after the first stage: `<first stage>-plan`, so
`dev-plan` with the default stages and `test-plan` with `stages: [test, prod]`, next to `prod-plan`.
This page writes `dev-plan` for the default stages. Up to v0.1.0-rc.2 it was `dev-plan` whatever the
first stage was called; see the [changelog](../../CHANGELOG.md) to move an estate whose first stage
is not `dev`.

A separate environment keeps PR plans out of the `dev` environment's history and approvals. Create
`dev-plan` with these variables (the same names `dev` uses): `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`, for a dedicated user-assigned managed identity with a federated credential
for the subject `repo:<owner>/<repo>:environment:dev-plan` (`environment:<first stage>-plan`). It must
not be the identity `dev` applies with.

Give that identity the least that lets a plan run:

- Azure: **Reader** on the dev subscription (or resource groups) and **Storage Blob Data Reader** on
  the dev state container only. No write role, no Key Vault secret access.
- Databricks: a minimal workspace entitlement that can read the objects the plan refreshes, with no
  cluster-create and no other entitlement.
- Plans run with `-lock=false` (input `lock: false`): a reader cannot write the state lock blob, and
  a PR plan must not block a real run.

**Be clear about what this protects.** The plan job runs the PR's own HCL, modules and providers.
That code can use the identity and can read dev state, including any secret that is stored in it.
Read-only limits what it can change, not what it can read. Treat dev state as readable by anyone who
can open a PR against the repository, keep secrets out of dev state, and keep dev data non-sensitive.

Recommended: restrict the `dev-plan` environment's deployment branches (to the PR branches your
platform lets you list, for example a protected naming pattern), where the platform supports it. The
trade-off: PR runs have refs like `refs/pull/<n>/merge`, which branch policies may not match, so a
strict policy can block every PR plan. Where it is too awkward, rely on the minimal role and on
requiring approval for workflows from first-time contributors. No reviewers are required on the
environment. The workflow reads no uat or prod variable and no secret.

## Shared units

A unit listed in `shared_units` exists under `prod` only and serves every stage
([the staged run](staged-run.md#units-that-serve-every-stage)), so the first stage's plan never shows it. To
let the reviewer see its change before merge, `iac-pr` plans it against prod, read-only:

- `ayasee changed-units --env prod --shared a,b` prints the shared units a PR changes: a change
  under one of them gives its name; a change inside `iac_root` but outside `units_root` (modules,
  `root.hcl`), a file under `<units_root>/prod/` that belongs to no unit, the configuration file, or
  a diff that cannot be computed gives every shared unit; changes to other stages and to the prod
  units that are not shared give nothing.
- Only then does `plan-shared` start, in the `prod-pr-plan` environment. Create it with
  `shared_units_pr_plan = true` in the [`github-environments`](../../modules/github-environments/README.md)
  module: it accepts PR branches and carries the **prod reviewers**, so a prod reviewer approves
  each run before the PR's code gets the prod identity. Give it its own identity (federated subject
  `repo:<owner>/<repo>:environment:prod-pr-plan`) with **read-only** rights on prod and on the prod
  state, no key access and no ledger access, through the same `AZURE_*` variables.
- The result goes to its own sticky comment, headed "Shared prod units plan".

Everything said above about `dev-plan` applies here with higher stakes: the PR's code runs with the
identity and can read prod state, including any secret stored in it (a tunnel's credentials, for
example). The reviewer approval on `prod-pr-plan` is what stands between an open PR and that read;
approve only after reading the diff. If that is not acceptable, leave `prod-pr-plan` out: the job
then waits and the change is seen first at the prod gate.

### With the workflow gate: `iac-pr-shared`

With `gate_mechanism: workflow` ([D15](../governance/decisions.md#d15-the-workflow-gate)) GitHub
has no reviewer to hold `plan-shared`, and nothing in the pull request's own run can: the pull
request controls that run's workflow file, and an environment open to pull-request branches hands
its credentials to any job that names it. So `iac-pr` never plans shared units in this mode. When
the pull request changes some, `shared-changes` warns and outputs `pending`, and `comment-shared`
writes the "Shared prod units plan" comment with the pull request number and head SHA to
authorise. The mode is read from the pull request's configuration and from its base commit's, so
a pull request cannot switch `plan-shared` back on. Each push rewrites the comment.

A prod reviewer who has read the diff then starts **`iac-pr-shared` on `main`**
(`gh workflow run iac-pr-shared.yml --ref main -f pr=<n> -f sha=<head sha>`):

| Job | Permissions | What it does |
|---|---|---|
| `verify` | `contents: read`, `pull-requests: read`, `actions: read` | No environment, no secret; checks out `main`. `ayasee pr-shared authorize` refuses unless the run is a `workflow_dispatch` on `refs/heads/main` and `github.actor` is `github.triggering_actor` (never re-run by someone else); the configuration **of `main`** has `gate_mechanism: workflow` and `shared_units` and lists the actor in `reviewers.prod`; the actor has write access; the pull request is open, from this repository (no fork), into `main`, and its head is exactly `sha`; the actor did not open it nor author or commit any of its commits (unless `solo_mode`, NON-COMPLIANT; a commit without a GitHub login is reported; 250 commits or more are refused); and `<environment_prefix>prod-pr-plan` deploys from `main` only. |
| `plan` | `contents: read`, `id-token: write` | Environment `prod-pr-plan` (named by `verify`, so the prefix is `main`'s). Refuses an attempt started by anyone but the reviewer `verify` accepted, checks out exactly `sha` (`persist-credentials: false`) and refuses any other checkout, then works like `plan-shared`: the changed shared units against the merge base with `main`, the plan (`binding-mode: replan`, `encrypt: false`, `lock: false`; never applied), the pack (stage `prod`) and the comment text as `ayasee-pr-comment-shared`. With no shared unit changed it neither logs in nor plans. |
| `comment` | `contents: read`, `pull-requests: write` | No pull-request code: checks out `main`. `ayasee pr-shared comment` posts the text as the `pr-plan-shared` sticky comment, headed by the commit planned and the reviewer (named, not mentioned), and says so when the pull request has moved on since: a new head needs a new run. |

Here `prod-pr-plan` carries **no reviewer and deploys from `main` only**: the branch policy,
which GitHub enforces, keeps every other branch's workflows away from its credentials, and the
checks run in `main`'s workflow file. Create it with `shared_units_pr_plan = true` and
`gate_mechanism = "workflow"` in the
[`github-environments`](../../modules/github-environments/README.md) module, with the same
read-only identity as above. This needs deployment branch policies: a public repository, or a
private one on GitHub Pro, Team or Enterprise. **On GitHub Free it is not available**: there
`prod-pr-plan` would accept every branch and could hold no credential of its own (no environment
secrets or variables), so the plan would run pull-request code with the prod apply's credentials.
The module refuses `shared_units_pr_plan` there, `iac-pr-shared` refuses to run and `ayasee doctor`
fails on its caller; the shared units are first seen at the prod approval issue after the merge.

The plan job runs on `main`'s ref, so the pull request's code runs in a job whose Actions cache
scope and OIDC `ref` claim are `main`'s: do not restore Actions caches in workflows that hold prod
credentials, and never federate a prod identity on a `ref:refs/heads/main` subject
([D15 known limits](../governance/decisions.md#d15-the-workflow-gate)).

`ayasee init` does not scaffold the caller; add it by hand, with the read-only secrets of
`prod-pr-plan` when `identity: secrets`:

```yaml
# .github/workflows/iac-pr-shared.yml
name: iac-pr-shared
on:
  workflow_dispatch:
    inputs:
      pr:
        description: Number of the pull request.
        required: true
      sha:
        description: Full head SHA of the pull request, as read.
        required: true
permissions: {}
jobs:
  plan:
    permissions:
      contents: read
      pull-requests: write
      actions: read
      id-token: write
    uses: tttao/ayasee/.github/workflows/iac-pr-shared.yml@<release SHA>
    with:
      pr: ${{ inputs.pr }}
      sha: ${{ inputs.sha }}
    secrets:
      # identity: secrets only: environment secrets of prod-pr-plan, read-only
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
```

## Fork pull requests

A fork PR gets a read-only token and no OIDC token. For those PRs `resolve`, `plan-first-stage`
and `plan-dev` are skipped
(`github.event.pull_request.head.repo.full_name == github.repository`), and so is `upload-sarif`;
fmt, validate, Trivy and Checkov still run, and their failures fail the check.

## Code scanning

`upload-sarif` publishes the findings to GitHub code scanning (the Security tab). Code scanning is
free on a public repository; a private one has it only with GitHub Code Security, an add-on of
GitHub Team and Enterprise, so on GitHub Free or Pro, and on Team without the add-on, every upload
fails ("Code scanning is not enabled for this repository") and `upload-sarif` is red on every pull
request. Such a repository turns the upload off in its caller:

```yaml
jobs:
  pr:
    uses: <owner>/ayasee/.github/workflows/iac-pr.yml@<sha>
    with:
      code-scanning: false   # no GitHub Code Security on this private repository
```

With `code-scanning: false`:

- `upload-sarif` is skipped, so no job with `security-events: write` runs;
- the scrubbed SARIF stays in the `ayasee-sarif` artifact of the run, for 30 days instead of one;
- the `checks` job prints a notice and writes a job summary saying code scanning is off, with the
  number of findings of each scanner (counts only; the rule ids and files are in the log of
  "List findings");
- Trivy and Checkov **still gate** `checks`: a high or critical finding still fails the check.

The nightly rescan takes the same input ([drift](drift.md#code-scanning-off)). The caller keeps
granting `security-events: write`: GitHub refuses a called workflow whose job asks for more than its
caller grants, whether or not that job runs.

The input is explicit rather than detected at run time. Treating the upload's "not enabled" error as
a pass would also hide a real misconfiguration (a token without the scope, a broken SARIF, an
outage) behind the same green, and would add logic to the one job that holds
`security-events: write`; an input is reviewed in the caller and printed on every run. `ayasee init`
writes `code-scanning: false` when GitHub says code scanning is not enabled, or for a private
repository on Free, Pro or Team where it cannot confirm GitHub Code Security (`--code-scanning` and
`--no-code-scanning` decide instead), and `ayasee doctor` warns when a caller uploads to a
repository without code scanning or where it cannot confirm it.

## Monorepos

When the IaC sits beside application code, set `iac_root` in `.ayasee.yaml` to the directory that
holds `root.hcl`, the modules and `units_root`:

```yaml
iac_root: infra/iac
units_root: infra/iac/stages
```

Then:

- the format checks, `terragrunt hcl validate`, Trivy and Checkov look only at `iac_root`, so a
  finding in an application Dockerfile or Compose file does not fail the IaC check, and the
  `ayasee-trivy` and `ayasee-checkov` SARIF categories hold IaC findings only. Checkov's
  `github_actions` framework is not run, because the workflows under `.github/workflows` belong to
  the applications too. The nightly rescan uses the same scope. The scanners report paths
  relative to `iac_root`, so `ayasee sarif-scrub --uri-prefix <iac_root>` rewrites them to
  repository paths and the alerts point at the right files;
- `changed-units` ignores a changed file outside `iac_root`, except the configuration file. A pull
  request that only touches application code gets the "No IaC changes" comment and no cloud login,
  and stays green, so `iac-pr` can be a required check. Do not add a `paths:` filter to the caller:
  a skipped workflow cannot satisfy a required check.
- give the `push` caller of `iac-staged` a `paths:` filter on the IaC, the configuration file and
  the ayasee callers (not all of `.github/`: an application workflow is not the IaC):

  ```yaml
  on:
    push:
      branches: [main]
      paths: ["<iac_root>/**", ".ayasee.yaml", ".github/workflows/iac-*.yml"]
  ```

  A push that touches only application code then starts no staged run. `resolve` refuses a commit
  that is no longer the tip of `main` only when a later commit changes the IaC (R70, issue #15):
  a change inside `iac_root` (documentation excepted), to the configuration file or to a caller
  `.github/workflows/iac-*.yml`: the same set as the filter, so that a commit the filter skips never
  supersedes a run that is waiting (it would start no run to promote the change). An IaC commit followed quickly by an application commit is therefore still promoted
  by its own run, which records its own SHA in the ledger (the commit it checked out, planned and
  applied; the cockpit counts the application commits after it as pending). The check compares
  the commit with the tip through the GitHub compare API and refuses on any doubt: an API error, a
  tip that does not descend from the commit, or a comparison of 300 files or more.

  Without the filter, every application push runs every stage: each pre-prod stage logs in, plans,
  applies nothing (unless an earlier IaC commit has not reached that stage yet) and writes its
  ledger record, and `prod-plan` logs in and plans. When prod is in sync, every unit plans clean
  and `prod-in-sync` records the SHA without asking for an approval
  ([the empty-plan path](staged-run.md#the-empty-plan-path)). When an IaC change is waiting at the
  prod gate, the application push supersedes it: its pre-prod stage records the newer SHA, the
  guard of the waiting run then refuses the approved apply as `superseded`
  ([the supersession guard](staged-run.md#the-supersession-guard)), and reviewers approve the same
  change again in the newer run. The filter avoids both. For deploys that are manual, see
  [deploys by hand only](staged-run.md#deploys-by-hand-only-workflow_dispatch).

`units_root` is relative to the repository root, not to `iac_root`, and must be inside it. Setting
`iac_root` alone fails every load of `.ayasee.yaml`, because the default `units_root`, `stages`, is
then outside it; the error says which path to write if the value was meant relative to `iac_root`
([the keys](../reference/ayasee-yaml.md#keys)).

Create the Checkov baseline with `--directory <iac_root>` (next section), because Checkov records
file paths relative to the directory it scanned.

## The Checkov baseline

Existing findings can be accepted once, so that only new ones fail a PR. Create the baseline from the
default branch and commit it:

```bash
uvx checkov@3.4.3 --directory . --framework terraform github_actions --create-baseline
mkdir -p .ayasee && mv .checkov.baseline .ayasee/checkov.baseline
```

With `iac_root` set (a monorepo), scan that directory instead and leave out `github_actions`:
`uvx checkov@3.4.3 --directory infra/iac --framework terraform --create-baseline`, then move
`infra/iac/.checkov.baseline` to `.ayasee/checkov.baseline`.

When `.ayasee/checkov.baseline` exists, the workflow passes `--baseline .ayasee/checkov.baseline`.
Use the Checkov version the workflow runs (input `checkov-version`) so the baseline format matches.

## SARIF never contains values

`ayasee sarif-scrub --in F --out F` rebuilds the file from an allow-list instead of removing known
fields. Kept: `version`, `$schema`; the tool driver and extensions (name, version, rules with id,
name, short description, default level); each result's rule reference, level, kind, partial
fingerprints (derived from rule, file and line when the scanner gave none) and locations reduced to a file URI plus line and column numbers; and per-run base URIs.
`result.message.text` is always the rule's short description (the rule id when there is none).
Snippets, region messages, code flows, fixes, properties, notifications, artifact contents and
anything else a scanner may add are dropped. The output keeps the fields GitHub's upload requires
(`version`, `$schema`, `runs[].tool.driver.name`, `results[].message.text`). Only scrubbed files leave
the job and the job log lists rule ids and file names only.
