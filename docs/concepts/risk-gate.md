# The risk gate

The prod gate is `always` by default (D9): a reviewer approves every prod run. What the risk gate
adds is **what the reviewer sees first**. It classifies every change in the prod plan into a risk
tier, puts the dangerous ones at the top and, for access resources, shows which principals gain or
lose access. Reviewing "update in place" on a grants resource tells you nothing; reviewing "`analysts`
loses `SELECT`" does.

Two independent mechanisms work together:

| Mechanism | Question | Effect |
|---|---|---|
| **Risk tiers** (`ayasee changeset`, rules in `rules/databricks-azure.yaml`) | How dangerous is this change? | Orders the summary, sets counts for the notification. Never blocks. |
| **Rego pack** (`policy/databricks/`, conftest) | Is this change allowed at all? | `deny` fails the job before the plan is uploaded. |

The code is `src/ayasee/risk.py` and `src/ayasee/changeset.py`.

## Tiers

| Tier | Meaning | Shown |
|---|---|---|
| `critical` | Destroys or replaces something whose loss is hard to undo | First, in the gate summary |
| `high` | Changes who can access what, or the isolation of data and networks | Second |
| `medium` | Can cause trouble, or bypasses a safety check | Third |
| `none` | Everything else that really changes | Last, collapsed under "other changes" |

No-op and read-only changes (and data sources) are not part of the change set at all.

### The built-in rules

Databricks publishes no official taxonomy. These rules are derived from the provider source and
documentation. They are in `rules/databricks-azure.yaml`.

| Rule id | Tier | Resource types | Actions | Condition |
|---|---|---|---|---|
| `dbx-workspace-destroy` | critical | `azurerm_databricks_workspace`, `databricks_metastore`, `databricks_metastore_assignment` | delete, replace, forget | |
| `dbx-storage-destroy` | critical | `databricks_storage_credential`, `databricks_external_location` | delete, replace, forget | |
| `force-destroy` | critical | any | delete, replace | `force_destroy` is true |
| `dbx-access` | high | `databricks_grants`, `databricks_grant`, `databricks_permissions`, `databricks_permission_assignment`, `databricks_mws_permission_assignment`, `azurerm_role_assignment` | any | |
| `dbx-isolation` | high | `databricks_workspace_binding`, `azurerm_private_endpoint`, `azurerm_subnet`, `azurerm_network_security_group`, `databricks_service_principal_federation_policy` | any | |
| `dbx-isolation-catalog` | high | `databricks_catalog` | any | `isolation_mode` differs between before and after |
| `dbx-compute-policy` | medium | `databricks_cluster_policy`, `databricks_sql_endpoint` | any | |
| `dbx-unsafe-skip-validation` | medium | any | any | `skip_validation` is true |
| `dbx-unsafe-force-update` | medium | any | any | `force_update` is true |
| `generic-destroy` | medium | any | delete, replace | Only if no other rule matched |

### How a change is matched

- A rule matches when the resource type matches (`*` matches any), **any** of the change's actions is
  in the rule's `actions`, and every `attr_true` and `attr_changed` condition holds. `any` means every
  action except `no-op` and `read`.
- A replace is `delete` plus `create`, so a replace matches rules for either. The item's tier is the
  **highest** tier among the rules that matched.
- `generic-destroy` is a fallback: it applies only to a delete or replace that matched no other rule.
- `moved`, `import` and `forget` are their own actions, never counted as create or delete. A `forget`
  of a critical type is critical because the critical rules list `forget`. A `forget` of the
  metastore types also trips the Rego rule `SG-DBX-005`.
- `attr_true` reads `after`, or `before` when the change has no `after` (delete, forget).

## Principal deltas

For `databricks_grants`, `databricks_grant`, `databricks_permissions` and `azurerm_role_assignment`
the change set lists **who gains and who loses access**, computed from the before and after values:

- `databricks_grants`: pairs of principal and privilege from each `grant` block.
- `databricks_grant`: principal and privileges.
- `databricks_permissions`: user, group or service principal and permission level.
- `azurerm_role_assignment`: principal ID and role.

`databricks_grants` is authoritative: an "update in place" can silently revoke access from other teams.
That is why a change that removes principals is `high` and lists the lost principals at the gate.

If a sensitive or unknown marker hides the principals in the block, **no delta is shown**; the tier
stays `high`. The delta is a convenience for the reviewer, never a substitute for reading the plan
when the gate says it cannot see.

Values are never read here beyond principal names, privilege names and role names. Backticks, line
breaks and HTML in addresses and principal names are neutralised before they reach a summary.

### What the reviewer sees

Real output of `ayasee changeset` on a grants update that removes `SELECT` from `analysts`:

```markdown
### Prod gate: 0 critical · 1 high · 0 medium · 1 changes

#### High

- `grants` `databricks_grants.x` (update) [dbx-access]
  - lost: `analysts` SELECT
```

The same summary is appended to the job summary of `prod-plan` and appears in the sticky comment on
the pull request (for the dev plan). The Teams `prod_waiting` card carries the counts
(critical, high, medium, total) and a link, never addresses or values.

A change to a root output alone is still a change (R72). The change set lists it as an item of kind
`output` (address `output.<name>`, tier `none`, never the value, sensitive or not), in the "other
changes" group of the summary. It counts in the total, so `prod-plan` reports `has-changes` and prod
goes through the gate instead of `prod-in-sync`; it does not count in the per-action resource
counts. Resource items carry kind `resource`.

## Rule files

An adopter can add rules or tighten existing ones with an extra rules file, using the same schema as
`rules/databricks-azure.yaml`:

```yaml
- id: dbx-compute-policy      # an existing id: allowed only at the same or a higher tier
  tier: high
  types: [databricks_cluster_policy, databricks_sql_endpoint]
  actions: [any]

- id: team-kms-key-change     # a new id
  tier: critical
  types: [azurerm_key_vault_key]
  actions: [delete, replace]
```

The loader (`load_rules`) applies the packaged file first and then each extra file in order:

- A later file may add rules, or replace a rule with the same id **at the same or a higher tier**.
- **A rule file can only raise a tier.** A rule that lowers an existing one is rejected with
  "adopter rules may only raise a tier" and the command fails.
- Unknown keys, an unknown action or a tier other than `critical`, `high` or `medium` are errors.

Use it with `ayasee changeset --rules FILE` (repeatable) or the `rules` input of the
`actions/risk` action. The `extra_rules` key of `.ayasee.yaml` is validated, but the shipped
workflows do not forward it yet, see [known limitations](architecture.md#known-limitations).

## The Rego pack

The Rego pack answers a different question. It runs `conftest` on the plan JSON **inside the job that
made the plan**, so the JSON never leaves the job, and a `deny` fails the job.

| Where | Stage in the data file | Effect |
|---|---|---|
| `iac-pr` `plan-dev` | `dev` | A deny fails the job; the sticky comment says "Policy pack: failed" with a link to the run |
| `iac-staged` `prod-plan` | `prod` | A deny stops the run before anything is uploaded |
| `iac-breakglass` | `prod` | A deny fails the run before the apply; it is the only pre-apply check on that path (R46) |

Six rules, `SG-DBX-001` to `SG-DBX-006`, cover the Unity Catalog and job-permission gaps that
Trivy and Checkov leave: `ALL_PRIVILEGES` to `account users`, non-isolated catalogs, public workspace
network access, `skip_validation`, metastore destruction and job permissions without a manager. Some
are `deny` in prod and `warn` elsewhere. Messages name the resource address only. The workflows pass no flags, so `allow-metastore-destroy` cannot be set from them. Rule details, the
data file and how to run it locally are in [the policy pack reference](../reference/policy-pack.md).

Trivy and Checkov remain in place for azurerm resources and workflow files. They have two Databricks
checks between them, both on `azurerm_databricks_workspace`, and none for the `databricks` provider.

## `gate: risky` is not supported

`gate: risky` would approve only runs with risky changes. It is deferred (D9, R35): `iac-staged`
rejects it with "gate: risky is not supported yet". The default is one approval per prod run.
