# The risk gate

The prod gate is `always` by default (D9): a reviewer approves every prod run. What the risk gate
adds is **what the reviewer sees first**. It classifies every change in the prod plan into a risk
tier, puts the dangerous ones at the top and, for access resources, shows which principals gain or
lose access. Reviewing "update in place" on a grants resource tells you nothing; reviewing "`analysts`
loses `SELECT`" does.

Two independent mechanisms work together:

| Mechanism | Question | Effect |
|---|---|---|
| **Risk tiers** (`ayasee changeset`, rules in `rules/databricks-azure.yaml` and `rules/cloudflare.yaml`) | How dangerous is this change? | Orders the summary, sets counts for the notification. Never blocks. |
| **Rego packs** (`policy/databricks/`, `policy/cloudflare/`, conftest) | Is this change allowed at all? | `deny` fails the job before the plan is uploaded. |

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

A second pack, `rules/cloudflare.yaml`, covers Cloudflare edge, DNS and Zero Trust Access. It is
loaded after the Databricks file, and both always load: a rule only matches its own types. Each
rule lists the provider v5 type names (`cloudflare_zero_trust_*`, `cloudflare_dns_record`) and the
v4 ones (`cloudflare_access_*`, `cloudflare_tunnel_*`, `cloudflare_record`); the table shows v5.

| Rule id | Tier | Resource types (v5) | Actions | Condition |
|---|---|---|---|---|
| `cf-access-destroy` | critical | `cloudflare_zero_trust_access_application` | delete, replace, forget | |
| `cf-access` | high | Access applications, policies, groups, identity providers and service tokens | any | |
| `cf-tunnel-ingress` | high | `cloudflare_zero_trust_tunnel_cloudflared_config`, `cloudflare_zero_trust_tunnel_cloudflared_route` | any | |
| `cf-firewall-destroy` | critical | `cloudflare_ruleset`, `cloudflare_firewall_rule`, `cloudflare_filter`, `cloudflare_access_rule`, `cloudflare_rate_limit`, `cloudflare_waf_*` | delete, replace, forget | |
| `cf-firewall` | high | the same | any | |
| `cf-dns-proxied` | high | `cloudflare_dns_record` | any | `proxied` differs between before and after |
| `cf-dns-mx` | high | `cloudflare_dns_record` | any | `type` is `MX` before or after |
| `cf-dns-apex` | high | `cloudflare_dns_record` | any | `name` is `@` before or after (a record named with the full zone name is not recognised) |

These rules come from the provider documentation; they have not been checked against a real
Cloudflare plan yet.

### How a change is matched

- A rule matches when the resource type matches (`*` matches any), **any** of the change's actions is
  in the rule's `actions`, and every `attr_true`, `attr_changed` and `attr_in` condition holds.
  `attr_in` maps an attribute to a list of strings and holds when the attribute's value in `before`
  or in `after` is one of them. `any` means every
  action except `no-op` and `read`.
- A replace is `delete` plus `create`, so a replace matches rules for either. The item's tier is the
  **highest** tier among the rules that matched.
- `generic-destroy` is a fallback: it applies only to a delete or replace that matched no other rule.
- `moved`, `import` and `forget` are their own actions, never counted as create or delete. A `forget`
  of a critical type is critical because the critical rules list `forget`. A `forget` of the
  metastore types also trips the Rego rule `SG-DBX-005`.
- `attr_true` reads `after`, or `before` when the change has no `after` (delete, forget).

## Principal deltas

For `databricks_grants`, `databricks_grant`, `databricks_permissions`, `azurerm_role_assignment`
and the Cloudflare Access policies and groups the change set lists **who gains and who loses
access**, computed from the before and after values:

- `databricks_grants`: pairs of principal and privilege from each `grant` block.
- `databricks_grant`: principal and privileges.
- `databricks_permissions`: user, group or service principal and permission level.
- `azurerm_role_assignment`: principal ID and role.
- `cloudflare_zero_trust_access_policy` and `cloudflare_zero_trust_access_group` (and the v4
  `cloudflare_access_policy` and `cloudflare_access_group`): each condition of `include`,
  `exclude` and `require` as a principal such as `email:alice@example.com`, `group:<id>`,
  `email_domain:example.com` or `everyone`, paired with `<decision>:<section>` (for example
  `allow:include`; a group has no decision and shows `member:include`). Inline `policies` of a v5
  Access application are not expanded.

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

When the change set touches a unit listed in `shared_units`, the summary opens with a
"Shared by every stage" line naming it, and each of its changes ends with **shared**
([units that serve every stage](staged-run.md#units-that-serve-every-stage)).

The same summary is appended to the job summary of `prod-plan` and appears in the sticky comment on
the pull request (for the pull-request plan). The Teams `prod_waiting` card carries the counts
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

The loader (`load_rules`) applies the packaged files first (`databricks-azure.yaml`, then
`cloudflare.yaml`) and then each extra file in order:

- A later file may add rules, or replace a rule with the same id **at the same or a higher tier**.
- **A rule file can only raise a tier.** A rule that lowers an existing one is rejected with
  "adopter rules may only raise a tier" and the command fails.
- Unknown keys, an unknown action or a tier other than `critical`, `high` or `medium` are errors.

Use it with `ayasee changeset --rules FILE` (repeatable) or the `rules` input of the
`actions/risk` action. The `extra_rules` key of `.ayasee.yaml` is validated, but the shipped
workflows do not forward it yet, see [known limitations](architecture.md#known-limitations).

## The Rego pack

The Rego packs answer a different question. The policy action runs `conftest` with every pack
(namespaces `ayasee.databricks` and `ayasee.cloudflare`) on the plan JSON **inside the job that
made the plan**, so the JSON never leaves the job, and a `deny` fails the job.

| Where | Stage in the data file | Effect |
|---|---|---|
| `iac-pr` `plan-first-stage` | the first stage (`dev` by default) | A deny fails the job and so `plan-dev`; the sticky comment says "Policy pack: failed" with a link to the run |
| `iac-staged` `prod-plan` | `prod` | A deny stops the run before anything is uploaded |
| `iac-breakglass` | `prod` | A deny fails the run before the apply; it is the only pre-apply check on that path (R46) |

Six rules, `SG-DBX-001` to `SG-DBX-006`, cover the Unity Catalog and job-permission gaps that
Trivy and Checkov leave: `ALL_PRIVILEGES` to `account users`, non-isolated catalogs, public workspace
network access, `skip_validation`, metastore destruction and job permissions without a manager. Some
are `deny` in prod and `warn` elsewhere. Messages name the resource address only. The workflows pass no flags, so `allow-metastore-destroy` cannot be set from them.
Five Cloudflare rules, `SG-CF-001` to `SG-CF-005`, deny a proxied record switched to DNS-only in
prod and warn about removed Access applications and firewall rules, new tunnel ingress and MX or
apex changes. Rule details, the data file and how to run the packs locally are in
[the policy pack reference](../reference/policy-pack.md).

Trivy and Checkov remain in place for azurerm resources and workflow files. They have two Databricks
checks between them, both on `azurerm_databricks_workspace`, and none for the `databricks` provider.

## `gate: risky` is not supported

`gate: risky` would approve only runs with risky changes. It is deferred (D9, R35): `iac-staged`
rejects it with "gate: risky is not supported yet". The default is one approval per prod run.
