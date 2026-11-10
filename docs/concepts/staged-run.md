# The staged run

`iac-staged` is a reusable workflow. A repository calls it once, on push to `main`, and one run
carries a commit through every stage: **dev, then uat, then a prod plan, then the gated prod
apply**. Nothing is re-planned silently between stages and nothing leaves a job unencrypted.

```yaml
# .github/workflows/iac.yml in the adopting repository
on:
  push:
    branches: [main]
jobs:
  iac:
    uses: <owner>/ayasee/.github/workflows/iac-staged.yml@<sha>
    with:
      start-stage: dev            # dev | uat | prod-plan
    secrets:
      teams-webhook: ${{ secrets.TEAMS_WEBHOOK }}   # optional
      ledger-token: ${{ secrets.LEDGER_TOKEN }}     # optional; git-branch:// ledger store only
    # never `secrets: inherit`
```

## Flow

| Job | Needs | Runs when | What it does |
|---|---|---|---|
| `resolve` | - | ref is `main` | Refuses with `superseded` when `github.sha` is no longer the tip of `main` (R70, `ayasee main-tip`: the run of the newer commit promotes the change; a redrive dispatches on the tip), refuses settings that are not supported yet (`ayasee config check-supported`: `gate: risky`, `engine: terraform`, `terragrunt: false`, `extra_rules`, `notify.teams: false`), validates `start-stage` (only `dev`, `uat` or `prod-plan`) and reads `.ayasee.yaml` (`stages`, `ledger.url`, `units_root`, `engine`). |
| `dev` | `resolve` | `start-stage` is `dev` | setup, guard, plan, apply (binding mode `same-job`), ledger write. |
| `uat` | `dev` | `start-stage` is `dev` or `uat`; `dev` succeeded, or was skipped by `start-stage` | Same as `dev`. |
| `prod-plan` | `dev`, `uat` | after `dev` and `uat` succeeded or were skipped by `start-stage` | setup, guard, plan (`encrypted_plan`), Rego pack and Trivy on the plan JSON, upload the artifact, notify `prod_waiting` when there are changes. Its token holds `contents: read`, `id-token: write` and `issues: read` only: it writes no ledger record, deployment or issue. A change to a root output alone counts as a change (R72). |
| `prod` | `prod-plan` | `has-changes == 'true'` | Gated by the `prod` environment: guard again, read the approver (fails when there is none), download, apply the approved plan, write the ledger, notify. |
| `prod-in-sync` | `prod-plan` | `has-changes == 'false'` | Runs in the `prod-plan` environment (no reviewers). Guard, then write the prod ledger record (and GitHub deployment) with every unit `no_changes` and converged. |

Every stage job has `environment: <name>`, an `ayasee-<repo>-<env>` concurrency group with
`cancel-in-progress: false`, and `github.ref == 'refs/heads/main'` in its `if:`. `prod-plan` runs in
its own `prod-plan` environment, so prod reviewers approve once, on the `prod` job, after the plan
has already been produced and scanned; `prod-in-sync` also runs in `prod-plan` and shares the
`prod` concurrency group with `prod` (both write the prod record).

Every plan and apply that takes the state lock (including the converge re-plan and the replan) passes
`-lock-timeout=<lock-timeout>`, default `10m` (`<n>s` or `<n>m`; set the `lock-timeout` input of the plan and
apply actions to change it). A nightly drift plan (see `drift.md`) holds the same lock, so an overlapping
run waits instead of failing on the blob lease. Plans run with `lock: false` do not use it.

A stage job is skipped only when `start-stage` says so. A skipped predecessor counts as passed only
if `start-stage` skipped it on purpose; a predecessor skipped because *its* predecessor failed
stops the chain.

dev and uat plan and apply in one job, so they use `binding-mode: same-job` with plan encryption
off: the plan file never leaves the job. A failed apply still writes the ledger (the units that
were applied are recorded) and then fails the job, opens or extends the `ayasee:stop-the-line`
issue for the environment, and sends a `stage_failed` notification. The stop-the-line issue blocks
later stages through the guard until it is closed. A prod job that stops before anything is
applied (stale or mismatched plan, or init or decrypt failing) is not an incident: no issue is opened; the job summary and the
`prod_failed` notification (status line "Plan refused: re-plan and approve again") say to re-run
`prod-plan` and approve again.

## Where the actions come from

A reusable workflow's `./actions/...` paths resolve in the *caller's* checkout, not in this
repository, so the workflow cannot use relative action paths when called from another repository.
Checking the repository out at the called workflow's own commit would solve this, but it is not
available everywhere: the `job.workflow_repository` and `job.workflow_sha` properties are
documented as not available on GitHub Enterprise Server ([contexts reference][ctx]), and
`github.job_workflow_sha` has been reported to be empty inside reusable workflows
([actions/runner#2417][issue]). So the workflow references its actions by repository path, with a
placeholder that the release process replaces:

```yaml
uses: OWNER/ayasee/actions/plan@0000000000000000000000000000000000000000 # AYASEE_REF
```

- **Release (option i).** `scripts/set-ayasee-ref.sh <owner/repo> <source-sha>` rewrites every
  `# AYASEE_REF` line to `<owner/repo>/actions/<x>@<source-sha>`. `<source-sha>` is the commit
  that holds the released code; the script refuses unless `git diff --quiet <source-sha> HEAD --
  actions/ policy/ src/` shows no difference, so the pinned actions are exactly the released code
  and the release commit differs only in the workflow file. Callers pin the release commit by SHA.
- **In-repo e2e (option ii).** `scripts/set-ayasee-ref.sh --local` rewrites the lines to
  `./actions/<x>`, for runs inside this repository where `./actions` is the checkout.

Until one of these has run, the committed workflow is a template and cannot be called. A test
checks that all placeholders share one ref.

[ctx]: https://docs.github.com/en/actions/reference/workflows-and-actions/contexts
[issue]: https://github.com/actions/runner/issues/2417

## Environments and their controls

The staged run uses four GitHub environments: `dev`, `uat`, `prod-plan` and `prod`. The whole set
the workflows expect is nine (add `dev-plan` for pull requests, `prod-breakglass` for emergencies, and
`drift-dev`, `drift-uat` and `drift-prod` for the nightly check); the
[`github-environments`](../../modules/github-environments/README.md) module creates all of them, and
[architecture](architecture.md#environments-and-identities) lists what each may do. On every staged
environment:

- **Deployment branches: `main` only.** This is the real control. The `if: github.ref ==
  'refs/heads/main'` in each job is only defence in depth.
- **Admins cannot bypass** protection rules (D2).

On `prod`: **required reviewers** and **`prevent_self_review: true`** (D2), so the person who
merged cannot approve the production apply. `dev`, `uat` and `prod-plan` have no reviewers. The
`prod` job reads the approvers from the approvals API and refuses to apply when there are none.

## Environment variables (per environment)

There are no cloud secrets. Set these as **variables** on each GitHub environment, each backed by
its own Entra user-assigned managed identity with a federated credential for
`environment:<name>`:

| Variable | Purpose |
|---|---|
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | `azure/login` (OIDC), and `ARM_CLIENT_ID`, `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID` for the provider |
| `AYASEE_LEDGER_URL` | Ledger store URL. Falls back to `ledger.url` in `.ayasee.yaml`; the run fails if neither is set. |
| `AYASEE_KEY_VAULT_KEY_ID` | Azure Key Vault key URL (`https://<vault>.vault.azure.net/keys/<name>`, no key version) protecting plan encryption. Set the same key on `prod-plan`, `prod` and `prod-breakglass`. `prod-plan` needs `wrapKey` and `unwrapKey` (it encrypts its plan, then decrypts it for `tofu show -json`), `prod` needs `unwrapKey`, `prod-breakglass` both. Full table: [azure-stage-identity](../../modules/azure-stage-identity/README.md#role-assignments-not-created-here). |

The `prod-plan` identity reads and plans prod; it must not be able to apply. It is also granted
write on the prod ledger record, because `prod-in-sync` records an unchanged prod from that
environment.

The workflow sets `ARM_USE_OIDC=true` and `DATABRICKS_AUTH_TYPE=github-oidc-azure`. Two optional
secrets are declared explicitly under `on.workflow_call.secrets`, never inherited: `teams-webhook`
(without it, notifications are skipped) and `ledger-token`, a fine-grained token with contents
write on the repository, needed only for a `git-branch://` ledger store. Jobs keep `contents: read`;
when `ledger-token` is set, only the guard and ledger steps receive it, as step-level environment
(`GIT_CONFIG_*` with an inline credential helper that reads the token from an environment
variable; no file, no global git configuration).

## The plan artifact

`prod-plan` uploads one artifact, `ayasee-prod-plan`, kept 7 days. It holds only the encrypted
plan files (`plans/<unit>.tfplan`) and `manifest.json` (plan hashes, engine version, provider lock
hashes). The plan JSON stays in the job: the Rego pack (conftest, with a data file that sets the
stage to `prod`) and the Trivy `config` scan (HIGH and CRITICAL, exit code 1) both run on it before
the upload, and the Trivy report is written to a file that is never uploaded; only rule ids and
severities are printed. A finding stops the run before anything is uploaded.

`prod-plan` outputs `has-changes`, `critical`, `high` and `units` (a JSON array of unit names).
The `prod` job downloads the artifact and applies those exact files after checking their hash,
the engine version and the provider lock hash. A stale or mismatched plan stops the run: re-plan
and approve again.

## Approval and the ledger

`prod` waits on the `prod` environment's required reviewers. Right after the guard, before the
artifact is downloaded or anything is applied, it asks the approvals API who approved `prod` on
this run (`ayasee approvals list --env prod`): every distinct login with an `approved` entry for
`prod`, sorted and comma-joined (`alice,bob`). It fails closed if there is none. Rejected entries
are never listed. The API has no attempt field, so approvals from earlier attempts of the same run
may be included: the recorded approver string is a superset of the people who approved this
attempt. It is stored in the ledger with `--approver`. The ledger in the backend is the source of
truth for what is live; the GitHub deployment is a projection written with `--mirror-deployments`.

## The empty-plan path

When every unit plans clean, `has-changes` is `false`: `prod` is skipped and `prod-in-sync` runs
instead. It runs in the `prod-plan` environment, so it never waits for prod reviewers. It still
passes the guard, then writes the prod ledger record (and the deployment projection) at this SHA
with every unit `no_changes` and converged, built from the `units` output (an empty list is
refused). Drift checks therefore never keep planning an older SHA. A failure notifies
`prod_failed`.

## Redrive

To resume from the middle of a promotion, call the workflow with `start-stage: prod-plan` (for
example after fixing a prod-plan failure with `uat` already live at the SHA). Earlier jobs are
skipped but the guard still runs in `prod-plan`, so the run is refused unless the preceding stage
has this SHA live in the ledger. Re-running failed jobs of an existing run reuses its artifact for
7 days.

## The supersession guard

Before each stage, `ayasee guard` checks the ledger: the preceding stage must have this SHA
live and converged (a different converged SHA there means `superseded`), and no open
`ayasee:stop-the-line` issue may exist for the stages up to prod. The `prod` job runs the guard
again after the approval wait, at apply time: a run that was approved while newer commits reached
uat is rejected as `superseded` and is not applied.

## Dependencies between units (R52)

A unit that reads another unit's outputs (a Terragrunt `dependency` block) is planned with the
outputs its upstream has *before* the upstream is applied. How the run handles that depends on the
binding mode:

- `same-job` (dev, uat) and `breakglass`: the plan made by the plan action is a preview. The apply
  action works one unit at a time in dependency order: it plans the unit again, applies that fresh
  plan, then runs the converge check, so every dependent is planned with the upstream's new outputs. In break-glass the policy pack
  checked the preview, so the fresh plan of each unit must propose the same `(address, actions)`
  set; otherwise the run stops before that unit's apply ("plan changed since the policy check; re-run
  break-glass"). Dev and uat have no policy gate and apply the fresh plan.
- `encrypted_plan` and `replan` (prod): the approved plan cannot be remade. `prod-plan` therefore
  refuses with `cascading change: <dependent> depends on <upstream>, which also changes; land
  <upstream> first` when a unit with changes reads, directly or through other units, the outputs of
  another unit with changes. Land the upstream unit in its own merge. Units that only list another
  unit under `dependencies` (ordering, no outputs) are not affected.
  `allow_cascade: true` in `.ayasee.yaml` turns the refusal off. It is unsafe: the converge check
  after each apply still catches a dependent that no longer matches the code, but only after the
  upstream was applied.

## A rejected prod gate

When a reviewer rejects the `prod` environment the job ends without running any step, so it cannot
notify. The `prod-result` job (runs when `prod` failed and there were changes) reads the run's
approvals with `ayasee approvals rejected --env prod --job prod` and posts `prod_rejected` when the gate was
rejected. The approvals API has no attempt field, so a rejection from an earlier attempt is only
counted when the `prod` job of the latest attempt ran no step. An apply failure is not announced there: the `prod` job already posts `prod_failed`.
