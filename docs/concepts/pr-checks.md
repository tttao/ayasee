# Pull-request checks

`iac-pr` is a reusable workflow for pull requests. It runs static checks and scans, plans the
changed units of the **first stage** (`dev` by default) with a read-only identity, runs the policy pack on that plan and posts one
sticky comment with the risk summary. **It never applies**: no apply, no guard, no ledger write, and
nothing the plan produced is uploaded. The only artifacts are the scrubbed SARIF and the comment
text, both kept for one day. Running a job in the
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
| `checks` | `contents: read` | `tofu fmt -check -recursive`, `terragrunt hcl format --check`, `terragrunt hcl validate`; Trivy `config` on `iac_root` (the repository by default; SARIF, no cache); Checkov via `uvx checkov@<version>` on `iac_root` (SARIF, with `--baseline .ayasee/checkov.baseline` when the file exists). Both scanners run even if an earlier step failed, so all SARIF is kept; the job still fails if any step failed. Each SARIF goes through `ayasee sarif-scrub` and the scrubbed files are kept as artifact `ayasee-sarif` (1 day). |
| `upload-sarif` | `security-events: write`, `actions: read`, `contents: read` | No checkout. Downloads `ayasee-sarif` and uploads it with `github/codeql-action/upload-sarif`, categories `ayasee-trivy` and `ayasee-checkov`. Skipped for fork PRs. |
| `resolve` | `contents: read` | No environment, no identity. Checks out the PR head and prints the environment `plan-first-stage` runs in with `ayasee config pr-plan-environment`: `<first stage>-plan` (`dev-plan` by default, `test-plan` with `stages: [test, prod]`). It fails when the configuration does not load, the first stage is `prod` or a stage name is reserved (`prod-pr`, `prod-breakglass`, `drift-*`, `*-plan`); `plan-first-stage` is then skipped, `plan-dev` fails and `comment` posts "the configuration was refused". |
| `plan-first-stage` | `contents: read`, `id-token: write` | Environment `<first stage>-plan`, from `resolve` (a job's environment is fixed before its steps run). Checks out the PR head (`persist-credentials: false`), finds the changed units of the first configured stage (`dev` by default), logs in to Azure when `identity` is `oidc` (with `identity: secrets` the plan step receives that environment's provider tokens instead, so keep them read-only: pull-request code runs with them), plans (`binding-mode: same-job`, `encrypt: false`, `lock: false`; plan files never leave the runner), runs the Rego pack on the plan JSON (with that stage) and writes the comment text, headed "Plan of `<stage>`", to artifact `ayasee-pr-comment` (1 day). |
| `plan-dev` | none | The verdict of the pull-request plan, and the check to require. No checkout, no environment, no identity. It runs whenever `resolve` ran, also after a failure, and fails unless `resolve` succeeded and `plan-first-stage` succeeded. A skipped job counts as passed in branch protection, so requiring `plan-first-stage` alone would let a refused configuration merge. |
| `shared-changes` | `contents: read` | Only when `shared_units` is set. No environment, no identity: works out which shared prod units the PR changes (`ayasee changed-units --env prod --shared ...`). |
| `plan-shared` | `contents: read`, `id-token: write` | Only when `shared-changes` found some. Environment `prod-pr-plan`. Plans those units against prod (`binding-mode: replan`, `encrypt: false`, `lock: false`; never applied, the plan files are deleted), runs the pack (stage `prod`) and keeps the comment text as `ayasee-pr-comment-shared`. See [shared units](#shared-units). |
| `comment-shared` | `contents: read`, `pull-requests: write` | Like `comment`, for a second sticky comment (`--marker pr-plan-shared`), so the first stage's comment never waits for the `prod-pr-plan` approval. |
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

## Fork pull requests

A fork PR gets a read-only token and no OIDC token. For those PRs `resolve`, `plan-first-stage`
and `plan-dev` are skipped
(`github.event.pull_request.head.repo.full_name == github.repository`), and so is `upload-sarif`;
fmt, validate, Trivy and Checkov still run, and their failures fail the check.

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
  the workflows:

  ```yaml
  on:
    push:
      branches: [main]
      paths: ["<iac_root>/**", ".ayasee.yaml", ".github/**"]
  ```

  A push that touches only application code then starts no staged run. `resolve` refuses a commit
  that is no longer the tip of `main` only when a later commit changes the IaC (R70, issue #15):
  a change inside `iac_root` (documentation excepted), to the configuration file or under
  `.github/`. An IaC commit followed quickly by an application commit is therefore still promoted
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
  change again in the newer run. The filter avoids both.

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
