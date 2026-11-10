# Onboard a brownfield Databricks estate

You already have workspaces, a Unity Catalog metastore, catalogs, external locations and grants that
were created by hand or by a tool you are leaving. This guide brings them under Terraform and
ayasee **without recreating or changing them**. The method: generate code with the Databricks
exporter, adopt the objects with import blocks, and require a **no-op plan in every stage** before you
trust the result.

> **The exporter is experimental.** The Databricks provider's exporter is labelled "experimental and
> provided as is". Treat its output as a draft to review line by line, never as finished code. The
> service names (`uc`, `idfed`) and flags can change between provider versions; read the
> [exporter guide](https://github.com/databricks/terraform-provider-databricks/blob/main/docs/guides/experimental-exporter.md)
> for the provider version you pin.

Read [the ownership contract](../../examples/databricks-azure/OWNERSHIP.md) first. It says which objects
Terraform owns (workspace, metastore assignment, catalogs, storage credentials, external locations,
grants) and which belong to Declarative Automation Bundles (jobs, pipelines, notebooks, models). Only
export what Terraform will own. The metastore itself is account-level and created once by an account
admin; Terraform owns its assignment, not its creation.

## Why this is risky, and what protects you

| Hazard | Protection |
|---|---|
| `databricks_grants` is **authoritative**: it overwrites existing grants, so an incomplete import revokes access on the next apply | Import every grant on the catalog; read the principal deltas at the gate ([risk gate](../concepts/risk-gate.md)) |
| `databricks_permissions` overwrites the ACLs of the same object type | Same: import completely, then review the delta |
| A replaced or destroyed workspace, metastore assignment, storage credential or external location | Critical tier at the gate; policy rule `SG-DBX-005` denies destroying metastore types ([policy pack](../reference/policy-pack.md)) |
| Provider releases are frequent and some minors remove attributes | Commit `.terraform.lock.hcl` per unit, pin versions, review provider updates by hand |
| Multiple permission levels for one identity cause permanent drift | Resolve them before import; use noise paths for the rest |

## 1. Prepare

- Complete the [quickstart](quickstart.md) up to the first pull request, with **empty or placeholder**
  units. The pipeline, identities and ledger must work before you import anything.
- Pin the Databricks provider version and use the same one for the export and for the units.
- Take the export per stage. `dev`, `uat` and `prod` each have their own workspace and objects, so
  each is exported and adopted separately. Start with `dev`.

## 2. Export with the experimental exporter

The exporter is part of the provider binary. For Unity Catalog and identity-federation objects use the
services `uc` and `idfed`:

```bash
export DATABRICKS_HOST=https://adb-0000000000000000.0.azuredatabricks.net
# authenticate as you would for the provider (the CI path uses DATABRICKS_AUTH_TYPE=github-oidc-azure)
./terraform-provider-databricks exporter -skip-interactive \
  -services uc,idfed -listing uc,idfed \
  -directory ./export-dev -native-import
```

`-skip-interactive` runs non-interactively, `-services` and `-listing` select what is exported and
listed, `-directory` is the output, and `-native-import` writes native `import` blocks (OpenTofu 1.5
or later) instead of relying on `terraform import` commands. Do not use `-export-secrets`: it writes
secret values to a `terraform.tfvars` file. With identity federation enabled the exporter emits
account-level objects as data sources rather than resources, which is what you want here; account-level
assignments are exported separately at the account level (see the exporter guide).

Commit nothing from `./export-dev` yet. It may contain more than you want to own.

## 3. Turn the export into a unit

Review the generated `.tf` files and move what Terraform should own into the unit layout:

```
shared/unity-catalog/main.tf        # the resources you keep, with variables for stage differences
stages/dev/unity-catalog/terragrunt.hcl   # inputs for dev
```

Rules while editing:

- Replace hard-coded IDs with variables or `dependency` outputs, and keep the stage differences in
  `terragrunt.hcl` inputs only. Keep secrets and tokens out of the code.
- Keep one `import` block per adopted object next to the resource it adopts. Delete everything the
  ownership contract assigns to Bundles.
- Split into units the way the example does (`workspace`, `unity-catalog`, `grants`). Units that read
  each other's outputs must land in **separate merges** because `prod-plan` refuses a cascade: adopt
  `workspace` first, then `unity-catalog`, then `grants`, each across all stages
  ([plan binding](../concepts/plan-binding.md#the-cascade-rule-r52-r53)).

## 4. The first plan must be a no-op

An adopted object must plan as **import, then nothing**. Open a pull request with the unit for `dev`.
`iac-pr` plans the changed dev unit with a read-only identity and comments the change set. What you
want to see, and what to do otherwise:

| Plan shows | Meaning | Action |
|---|---|---|
| `import` only | The object is adopted unchanged | Good |
| `update` after import | Code and reality differ | Fix the code to match reality. Do **not** apply an update to "fix" production. |
| `create` | The import did not match an existing object | Fix the address or ID in the import block |
| `delete` or `replace` | The code would destroy something | Stop. Critical tier. Find out why before any merge. |
| `update` on `databricks_grants` that removes a principal | The import list is incomplete | Add the missing grants. The gate shows who would lose access. |

Then merge. The `dev` stage applies the imports, writes the ledger, and `uat` and `prod-plan` repeat
the same for their own stage folders. The prod gate shows `import` actions as their own action, not as
creates, and still asks for an approval. After the apply, the **second** plan of each stage must show
no changes; the converge check requires exactly that, and a unit that does not converge stops the run.

Work through `dev`, then `uat`, then `prod`, each with its own exported inputs. Repeat the cycle for
each unit.

A follow-up pull request can remove the `import` blocks once the objects are in state; they are
harmless to keep but they are noise in later plans.

## 5. Drift noise lists

After adoption, the nightly drift check ([drift](../concepts/drift.md)) will report any difference
between state and the cloud. Some Databricks resources change on their own or carry attributes the
provider reports unstably. Rather than ignore drift on whole resources, list the **attribute paths**
that are noise:

```yaml
# .ayasee.yaml
drift_noise:
  databricks_permissions:
    - access_control          # covers access_control.0.permission_level and below
  databricks_job:
    - email_notifications
```

An update is ignored only if **every** changed attribute path falls under a noise path for that
resource type. A create, delete, replace, import, forget or a pending move is always drift. Add a noise
path only after you have seen the same difference twice and decided it is harmless; the drift issue
lists the addresses, so you can read the attribute difference in a plan yourself.

## 6. Hand the rest to Bundles

Anything the contract gives to Bundles stays out of Terraform. If you imported an object that belongs
to a Bundle by mistake, hand it over with the three-step procedure in
[the ownership document](../../examples/databricks-azure/OWNERSHIP.md#handing-an-object-over): a
`removed` block with `destroy = false`, then bind it in the bundle, never one commit that does both.

## Checklist

- [ ] Provider version pinned and locked in every unit
- [ ] Export reviewed; no secrets exported; only Terraform-owned objects kept
- [ ] First plan per stage is import-only; second plan is empty
- [ ] Grants imported completely; no principal removed at the gate by accident
- [ ] Units landed one per merge, upstream first
- [ ] Drift noise paths added only where the difference was understood
