# Databricks on Azure: reference adopter repository

A working layout for a platform team that runs Databricks workspaces on Azure with
[ayasee](../../README.md): OpenTofu and Terragrunt units, promoted `dev` then `uat` then `prod` by
GitHub Actions, with a gated prod apply of the exact approved plan. Copy this directory to a new
repository, replace the placeholders and follow the steps below.

```
.ayasee.yaml              stages, engine, ledger, cascade rule
root.hcl                     azurerm backend (OIDC + Entra auth), keyed per stage and unit
shared/<unit>/main.tf        the OpenTofu code of each unit, written once
stages/<stage>/<unit>/       terragrunt.hcl (inputs) + .terraform.lock.hcl, for 3 stages x 3 units
bootstrap/                   one-off root: GitHub environments, identities, environment variables
.github/workflows/           callers of the ayasee reusable workflows
OWNERSHIP.md                 what Terraform owns and what Bundles own (D13)
renovate.json                provider and action updates
```

| Unit | Resources | Depends on |
|---|---|---|
| `workspace` | `azurerm_databricks_workspace`, VNet-injected, no public IPs, `public_network_access_enabled = false` | - |
| `unity-catalog` | metastore assignment, storage credential, external location, `ISOLATED` catalog and its workspace binding | `workspace` (id and url) |
| `grants` | `databricks_grants` on the catalog | `unity-catalog` (catalog name, url) |

Authentication holds no secret: the workflows set `ARM_USE_OIDC` and
`DATABRICKS_AUTH_TYPE=github-oidc-azure`, and each GitHub environment is backed by its own Entra
managed identity (D7). The HCL contains only names, ids and URLs.

Everything here uses placeholders: `OWNER` (your GitHub owner), the all-zero subscription and tenant
GUIDs, `stgayaseestate` (state storage) and `stgayaseeledger` (ledger storage). Search for
them before you start.

## Adoption steps

### 1. Bootstrap

Create the GitHub environments, one managed identity per environment and the environment variables
(`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, and later `AYASEE_LEDGER_URL` and
`AYASEE_KEY_VAULT_KEY_ID`; no secrets, R30). The environments are `dev-plan`, `dev`, `uat`,
`prod-plan`, `prod`, `prod-breakglass`, `drift-dev`, `drift-uat` and `drift-prod`.

```bash
cd bootstrap
az login                        # a person who can create identities in the resource group
export GITHUB_TOKEN=...         # can administer the repository
tofu init
tofu apply -var github_owner=OWNER -var github_repository=my-platform \
  -var tenant_id=... -var subscription_id=... -var resource_group_name=rg-ayasee-identities \
  -var location=westeurope \
  -var 'prod_reviewers={teams=[101],users=[]}' -var 'breakglass_reviewers={teams=[202],users=[]}'
```

`prod` and `prod-breakglass` need different, non-empty reviewer sets; the environments forbid self
review and admin bypass (D2). Keep the state of this root outside the stage units' state. The
`client_ids` and `principal_ids` outputs are what the next step needs. See
[github-environments](../../modules/github-environments/README.md) and
[azure-stage-identity](../../modules/azure-stage-identity/README.md).

### 2. Role assignments

The identities have no rights yet. Assign them with the `principal_ids` output, using the minimum
table of the [azure-stage-identity README](../../modules/azure-stage-identity/README.md#role-assignments-not-created-here):
`dev-plan` reads; `dev` and `uat` apply their stage and write the ledger; `drift-*` read and take
the state lease; `prod-plan` plans prod, takes the prod state lease, wraps **and** unwraps the plan
key (it runs `tofu show -json` on its own encrypted plan) and writes the prod ledger record, but
cannot apply; `prod` applies, unwraps and reads and writes the ledger; `prod-breakglass` has its
own identity with apply rights, wrap and unwrap, and ledger read and write. Use the
state containers `tfstate-dev`, `tfstate-uat` and `tfstate-prod` (one per stage, from `root.hcl`)
so that each identity only reaches its own stage. Set `AYASEE_STATE_STORAGE_ACCOUNT` and
`AYASEE_STATE_RESOURCE_GROUP` in `root.hcl` (or as environment variables) to your state account.
The Databricks side needs the same identities added as account-level and workspace-level admins
where they create catalogs and grants, and the access connector needs Storage Blob Data Contributor
on the catalog storage.

### 3. Ledger storage

The ledger is the source of truth for what is live in each stage. Create a storage account and a
container, give `dev`, `uat`, `prod-plan`, `prod`, `prod-breakglass` write (Storage Blob Data
Contributor) and `drift-*` read on it, then set `ledger.url` in `.ayasee.yaml` to
`azure-blob://<account>.blob.core.windows.net/<container>?prefix=<prefix>`. Optionally pass
`-var ledger_url=...` to the bootstrap to write it as the `AYASEE_LEDGER_URL` variable.

### 4. Key Vault key for plan encryption

The prod plan is encrypted before it leaves the `prod-plan` job (D1). Create a Key Vault key, give
`prod-plan` and `prod-breakglass` `wrapKey` and `unwrapKey`, and `prod` `unwrapKey` on it, and
re-run the bootstrap with `-var key_vault_key_id=https://<vault>.vault.azure.net/keys/<key>`
(no key version: the plan action rejects a versioned URL so that key rotation cannot break a pending approval).
It is written as `AYASEE_KEY_VAULT_KEY_ID` on those three environments.

### 5. Set the release SHA

The callers in `.github/workflows/` carry a placeholder: `OWNER/ayasee/...@0000000000000000000000000000000000000000 # replace with an ayasee release SHA`.
Pick an ayasee release, take the full 40-character commit SHA of its tag
(`git rev-parse vX.Y.Z^{commit}`), and replace both the owner and the pin. Never use a branch or tag name.

```bash
sed -i -E 's#OWNER/ayasee#my-org/ayasee#; s#@0{40} \# replace with an ayasee release SHA#@<40-hex SHA> \# ayasee vX.Y.Z#' \
  .github/workflows/*.yml
```

Callers never use `secrets: inherit`; each passes only what the called workflow declares
(`teams-webhook`, optional). Create the repository secret `TEAMS_WEBHOOK` or drop that line.
Renovate keeps the digests current (`renovate.json`).

### 6. First pull request

Open a pull request that changes a `stages/dev/*` file. `pr.yml` runs the checks and scans, plans
the changed dev units in the `dev-plan` environment and comments the change summary. Nothing is
applied. A change to `shared/`, `root.hcl` or `.ayasee.yaml` plans every dev unit.

Lock files: each unit has a `.terraform.lock.hcl`, committed. Regenerate them after changing a
provider constraint (this is `tofu providers lock` run in every unit), for linux and macOS:

```bash
cd stages/dev    # then copy the three lock files to uat and prod: same code, same locks
terragrunt run --all --non-interactive --no-auto-init -- providers lock \
  -platform=linux_amd64 -platform=darwin_arm64
```

### 7. First merge

**First-time adoption: three merges, one unit at a time.** Do not commit all nine unit directories
at once. On a first create every unit in prod is a change, `unity-catalog` depends on `workspace`
and `grants` on `unity-catalog`, so `prod-plan` refuses the plan as a cascade (R52). The reason is
real: a dependent's prod plan made before its upstream exists would be computed from mock upstream
outputs, not from the real workspace id or catalog name. So land them incrementally, each unit
across all stages (`stages/dev/<unit>`, `stages/uat/<unit>`, `stages/prod/<unit>`) in one pull request:

1. Merge 1: `workspace` in dev, uat and prod. Wait until it is live in prod.
2. Merge 2: `unity-catalog` in dev, uat and prod. Wait until it is live in prod.
3. Merge 3: `grants` in dev, uat and prod.

Setting `allow_cascade: true` is not the way to bootstrap: it switches the refusal off and leaves the
plan wrong for the dependents. Keep it `false`.


Merging to `main` starts `main.yml`: `dev`, `uat`, then `prod-plan`, which encrypts and scans the
prod plan and waits at the `prod` environment for a reviewer who is not the author. The reviewer
approves, and the exact approved plan is applied. Each stage writes the ledger. Nightly, `nightly.yml`
plans every stage for drift and opens an issue per environment.

**Landing dependent changes (R52).** `allow_cascade` is `false`. `unity-catalog` reads `workspace`
outputs and `grants` reads `unity-catalog` outputs, and a saved prod plan of a dependent was made
against the upstream's old outputs. A merge that changes a unit and a unit that depends on it is
refused at `prod-plan` ("cascading change: land the upstream unit first"). Land the workspace
change, wait for it to be live in prod, then merge the dependent catalog or grants change. In dev
and uat the units run in dependency order, so they are not affected.

### 8. Cockpit (optional)

`cockpit.yml` is a wrapper for the estate cockpit: one snapshot of the stages of all your
repositories. Follow [docs/cockpit/setup.md](../../docs/cockpit/setup.md) (GitHub App, data repository,
`repos.yaml`, visibility), then set `data-repository`. Delete `cockpit.yml` if you do not use it.
The cockpit reads only `github-branch://` ledgers: with the `azure-blob://` ledger of step 3 it
cannot show this repository, so move the ledger to a branch first if you want it in the cockpit.

## What each workflow does

| Caller | Reusable workflow | Trigger | What it does |
|---|---|---|---|
| `pr.yml` | `iac-pr` | pull request | Static checks, Trivy and Checkov scans (SARIF), dev-only plan with risk comment. Never applies. |
| `main.yml` | `iac-staged` | push to `main` | dev, uat, prod-plan, gated prod apply of the approved encrypted plan, ledger writes. |
| `nightly.yml` | `iac-nightly` | cron | Drift plan per stage in `drift-<stage>` (plan identity), issues, rescan of `main`. |
| `breakglass.yml` | `iac-breakglass` | manual | Emergency prod change: incident reference, `prod-breakglass` reviewers, post-approval issue. |
| `cockpit.yml` | `cockpit-collect` | `main` runs and cron | Estate snapshot and its renderer. Optional. |

See [docs/concepts](../../docs/concepts/staged-run.md) for the details and
[docs/compliance/dora-art17.md](../../docs/compliance/dora-art17.md) for the control mapping.

## Updating the Databricks provider

`renovate.json` raises provider and action updates as pull requests. The `databricks/databricks`
rule never automerges and adds the label `breaking-check`: read the provider changelog for
"Breaking" before merging, and let the PR plans of all stages show what changes.

## Checks

The repository's tests (`tests/examples/test_example_wiring.py`) run `terragrunt hcl format --check`
and `terragrunt hcl validate` on the stages, `tofu validate` on `bootstrap/` and the shared code, and
the Databricks policy pack (`conftest`, stage `prod`) on a canned prod plan and on a copy with public
network access that must fail.
