# Quickstart: from zero to a first green run

This guide takes you from nothing to a green staged run (dev, uat, an approved prod apply) on a
Databricks-on-Azure repository, using the bootstrap modules and the
[reference example](../../examples/databricks-azure/README.md). Expect an afternoon, most of it
spent on Azure role assignments.

ayasee is **pre-alpha**. Read the [known limitations](../concepts/architecture.md#known-limitations)
first. If you are not on GitHub Actions, OpenTofu with Terragrunt, and Azure for state, identities
and plan encryption, the wired path will not fit yet: see [the comparison](../comparison.md).

## What you need

- A GitHub repository you can administer, on github.com. GitHub Enterprise Server is unverified.
- A person with rights to create managed identities in an Azure resource group, and to assign roles.
- An Azure storage account for Terraform state, a second container for the ledger, and a Key Vault
  key for plan encryption.
- Locally: `tofu` (OpenTofu), `az`, and a GitHub token that can administer the repository. The
  workflows install OpenTofu 1.13.9 and Terragrunt 1.2.1 themselves.
- For Databricks: an account admin who created the Unity Catalog metastore once, outside Terraform
  (see [ownership](../../examples/databricks-azure/OWNERSHIP.md)).

## 0. Try the tooling without any cloud

The CLI runs locally on a saved plan JSON. This shows you what a reviewer will see at the prod gate.

```bash
git clone https://github.com/tttao/ayasee && cd ayasee
uv sync
mkdir -p /tmp/plans && cp tests/fixtures/plans/grants_update.json /tmp/plans/grants.json
uv run ayasee changeset --plan-json /tmp/plans --sha 0123456789ab --env prod \
  --out /tmp/changeset.json --summary /tmp/summary.md
cat /tmp/summary.md
uv run ayasee config show
```

The plan directory holds one `<unit>.json` per unit, in `tofu show -json` format. The summary lists
the change by risk tier and the principals that lose or gain access, see [the risk gate](../concepts/risk-gate.md).

To see the whole staged run on GitHub (PR plan, dev, uat, the prod gate, the ledger) without Azure,
spawn an [ephemeral sandbox](../../e2e/README.md#ephemeral-sandbox-one-command-no-cloud) with
`uv run python -m e2e.ephemeral up --use-gh-token` and delete it with `down` afterwards.

## 1. Start from the example

Copy `examples/databricks-azure/` into a new repository. It contains:

| Path | Purpose |
|---|---|
| `.ayasee.yaml` | Stages, engine, ledger URL, `allow_cascade: false` |
| `root.hcl` | The azurerm backend (OIDC and Entra authentication), keyed per stage and unit |
| `shared/<unit>/` | The OpenTofu code of three units: `workspace`, `unity-catalog`, `grants` |
| `stages/<stage>/<unit>/` | Terragrunt inputs and committed `.terraform.lock.hcl` for 3 stages by 3 units |
| `bootstrap/` | A one-off root that creates the GitHub environments, identities and variables |
| `.github/workflows/` | Callers of the ayasee reusable workflows |

Search for the placeholders and replace them: `OWNER`, the all-zero subscription and tenant GUIDs,
`stgayaseestate` (state storage) and `stgayaseeledger` (ledger storage).

## 2. Pin a ayasee release

The callers carry `OWNER/ayasee/...@0000000000000000000000000000000000000000`. A reusable workflow
cannot reference its own actions by a relative path from another repository, so the workflow files hold
a placeholder that a release rewrites (see
[where the actions come from](../concepts/staged-run.md#where-the-actions-come-from)). There is no
published ayasee release yet, so make one from your copy of the ayasee repository:

```bash
# in your ayasee checkout, on a clean commit that holds the code you want to release
src=$(git rev-parse HEAD)
scripts/set-ayasee-ref.sh my-org/ayasee "$src"
git commit -am "chore: pin actions to $src"      # this commit is the release commit
git rev-parse HEAD                                # the SHA your callers pin
git push origin HEAD                              # both commits must exist in the remote
```

The script refuses unless `actions/`, `policy/`, `src/`, `pyproject.toml` and `uv.lock` are identical
at `$src` and `HEAD`, so the pinned actions are exactly the released code. Callers pin that release
commit by full SHA, never by branch or tag. Do the same for `iac-pr.yml`, `iac-nightly.yml`,
`iac-breakglass.yml` and, if you use the cockpit, `cockpit-collect.yml`, by passing the file as the
last argument. Then, in your adopter repository:

```bash
sed -i -E 's#OWNER/ayasee#my-org/ayasee#; s#@0{40} \# replace with a ayasee release SHA#@<40-hex SHA> \# ayasee vX.Y.Z#' \
  .github/workflows/*.yml
```

Callers never use `secrets: inherit`.

## 3. Bootstrap the environments and identities

```bash
cd bootstrap
az login
export GITHUB_TOKEN=...      # can administer the repository
tofu init
tofu apply -var github_owner=my-org -var github_repository=my-platform \
  -var tenant_id=... -var subscription_id=... -var resource_group_name=rg-ayasee-identities \
  -var location=westeurope \
  -var 'prod_reviewers={teams=[101],users=[]}' -var 'breakglass_reviewers={teams=[202],users=[]}'
```

This uses the two modules:

- [`github-environments`](../../modules/github-environments/README.md) creates `dev-plan`, `dev`, `uat`,
  `prod-plan`, `prod`, `prod-breakglass` and `drift-dev`, `drift-uat`, `drift-prod`. Environments with
  reviewers get `prevent_self_review = true` and admins cannot bypass (D2). The `prod` and
  `prod-breakglass` reviewer sets must be non-empty and different.
- [`azure-stage-identity`](../../modules/azure-stage-identity/README.md) creates one managed identity and one
  federated credential per environment and exposes `client_id` and `principal_id`. Client IDs become
  the `AZURE_CLIENT_ID` environment variable. Keep the state of this root outside the stage units'
  state.

## 4. Assign roles

The identities have no rights yet. Use the `principal_ids` output and the minimum table in
[the identity module README](../../modules/azure-stage-identity/README.md#role-assignments-not-created-here):
`dev-plan` reads; `dev` and `uat` apply their stage and write the ledger; `drift-*` read and take
the state lease; `prod-plan` plans prod, takes the prod state lease, wraps **and** unwraps the plan
key (it runs `tofu show -json` on its own encrypted plan) and writes the prod ledger record, but
cannot apply; `prod` applies, unwraps and reads and writes the ledger; `prod-breakglass` has its own
identity with apply rights, wrap and unwrap, and ledger read and write. State
containers are one per stage (`tfstate-dev`, `tfstate-uat`, `tfstate-prod`) so an identity only
reaches its own stage. Add the identities to Databricks as admins where they create catalogs and
grants.

## 5. Create the ledger store and the plan key

- **Ledger.** A container; give `dev`, `uat`, `prod-plan`, `prod` and `prod-breakglass` Storage Blob
  Data Contributor and `drift-*` read. Set `ledger.url` in `.ayasee.yaml` to
  `azure-blob://<account>.blob.core.windows.net/<container>?prefix=<prefix>`. See [the ledger](../concepts/ledger.md).
- **Plan key.** A Key Vault key. Give `prod-plan` and `prod-breakglass` `wrapKey` and `unwrapKey`,
  and `prod` `unwrapKey`. Re-run the bootstrap with
  `-var key_vault_key_id=https://<vault>.vault.azure.net/keys/<key>`: **no key version**, so rotating
  the key does not break a pending approval. It is written as `AYASEE_KEY_VAULT_KEY_ID` on those
  environments.

## 6. The first pull request

Open a pull request that changes a file under `stages/dev/`. `pr.yml` runs `iac-pr`: formatting and
validation, Trivy and Checkov, the Rego pack, and a read-only plan of the changed dev units in the
`dev-plan` environment. It posts one comment with the change set. **Nothing is applied.** It is green
when the checks pass. See [pull-request checks](../concepts/pr-checks.md).

## 7. The first merge, one unit at a time

Do not add all nine unit directories at once. On a first create every unit in prod changes, and
`unity-catalog` reads `workspace` while `grants` reads `unity-catalog`, so `prod-plan` refuses the plan
as a cascade. Land one unit per pull request, across all stages:

1. Merge 1: `workspace` in dev, uat and prod. Wait until it is live in prod.
2. Merge 2: `unity-catalog` in dev, uat and prod. Wait until it is live in prod.
3. Merge 3: `grants` in dev, uat and prod.

Each merge starts `main.yml`, which runs `iac-staged`:

1. `dev` and `uat` plan and apply each unit and converge-check it.
2. `prod-plan` plans prod, scans it, encrypts the plans and writes the manifest. The job summary shows
   the [risk gate](../concepts/risk-gate.md).
3. The `prod` environment waits. A reviewer who is **not** the person who merged approves it.
4. `prod` applies the exact approved plan files, converge-checks each unit and writes the ledger.

A green run ends with prod applied and the ledger showing the merge SHA in all three environments:

```bash
ayasee ledger show --store "azure-blob://<account>.blob.core.windows.net/<container>?prefix=<prefix>" \
  --repo my-org/my-platform --env prod
```

## 8. What runs afterwards

- `nightly.yml` plans every stage at its live SHA and opens a drift issue per environment, see
  [drift](../concepts/drift.md).
- `breakglass.yml` is the emergency path, see [the DORA mapping](../compliance/dora-art17.md).
- Optional: `cockpit.yml`, the estate view. Follow [cockpit setup](../cockpit/setup.md).

## If something is red

| Symptom | Likely cause |
|---|---|
| `prod-plan` fails with "cascading change" | You changed a unit and one that reads its outputs in the same merge. Land the upstream first. |
| `prod` fails with "approved plan no longer matches" | The plan is stale or was altered. Re-run `prod-plan` and approve again. See [plan binding](../concepts/plan-binding.md). |
| A stage is refused with `superseded` or `predecessor_missing` | The preceding stage does not have this SHA live. See [the ledger](../concepts/ledger.md#who-writes-and-who-reads). |
| A stage is refused with `stop_the_line` | An open `ayasee:stop-the-line` issue. Repair, then close it. |
| Azure login succeeds but the token exchange fails with no message | The federated credential subject is wrong. Entra accepts a wrong subject at creation and then fails silently; check it matches `repo:<owner>/<repo>:environment:<name>`. |
