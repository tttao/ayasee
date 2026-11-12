# Policy packs

Rego rules that run on the plan JSON (`tofu show -json`) inside the PR, prod-plan and break-glass jobs. There are
two packs, and the policy action runs both on every plan: the Databricks pack in `policy/databricks/` (conftest
namespace `ayasee.databricks`) and the [Cloudflare pack](#cloudflare-pack) in `policy/cloudflare/` (namespace
`ayasee.cloudflare`). A pack only looks at its own resource types.

## Databricks pack

### Coverage

| Scanner | Databricks checks | Notes |
|---------|-------------------|-------|
| Trivy | 0 | No Databricks checks exist. |
| Checkov | 2 | `CKV_AZURE_158`, `CKV2_AZURE_48` (workspace-level Azure settings). |
| This pack | 6 | `SG-DBX-001` to `SG-DBX-006`, evaluated on the plan, so they see the final `after` values. |

The pack does not replace Trivy or Checkov. It covers the Unity Catalog and job-permission gaps they leave.

### Rules

Every message starts with the rule id and names the resource address only. No attribute values appear in messages,
apart from the principal or privilege names a rule is about.

| Id | Severity | Triggers when |
|----|----------|---------------|
| `SG-DBX-001` | deny | A `databricks_grants` entry (`after.grant[]`) or a `databricks_grant` grants `ALL_PRIVILEGES` to the principal `account users`. |
| `SG-DBX-002` | deny in prod, warn elsewhere | A `databricks_catalog` is created or updated with `isolation_mode` other than `ISOLATED`. |
| `SG-DBX-003` | deny in prod, warn elsewhere | An `azurerm_databricks_workspace` has `public_network_access_enabled = true`. |
| `SG-DBX-004` | warn | A `databricks_external_location` or `databricks_storage_credential` sets `skip_validation = true`. |
| `SG-DBX-005` | deny | A resource whose type starts with `databricks_metastore` is deleted (including replace) or forgotten, unless the flag `allow-metastore-destroy` is set. |
| `SG-DBX-006` | deny | A `databricks_permissions` for a job has no `CAN_MANAGE` entry and no `IS_OWNER` entry in `access_control`. `IS_OWNER` counts as a holder. A permission is a job permission when `job_id` is known, or unknown until apply (`after_unknown.job_id` is true, for example `job_id = databricks_job.x.id` in the same plan). Cluster, notebook and other permissions never match. |

## Cloudflare pack

Edge, DNS and Zero Trust Access, for provider v5 type names and their v4 equivalents. Messages name the resource
address only, never a hostname, service or record value. These rules come from the provider documentation and
have not been checked against a real Cloudflare plan yet.

| Id | Severity | Triggers when |
|----|----------|---------------|
| `SG-CF-001` | deny in prod, warn elsewhere | A `cloudflare_dns_record` (v4 `cloudflare_record`) is updated from `proxied = true` to `proxied = false`: DNS-only publishes the origin address. |
| `SG-CF-002` | warn | A `cloudflare_zero_trust_access_application` (v4 `cloudflare_access_application`) is deleted, replaced or forgotten: the login in front of its origin goes away. |
| `SG-CF-003` | warn | A `cloudflare_ruleset`, `cloudflare_firewall_rule`, `cloudflare_filter`, `cloudflare_access_rule`, `cloudflare_rate_limit` or `cloudflare_waf_*` is deleted, replaced or forgotten, or a `cloudflare_ruleset` update leaves fewer `rules` than before. |
| `SG-CF-004` | warn | A tunnel config (`cloudflare_zero_trust_tunnel_cloudflared_config`, v4 `cloudflare_tunnel_config`) has an ingress (hostname and service pair) after the change that it did not have before, or a tunnel route is created or updated. |
| `SG-CF-005` | warn | A DNS record whose `type` is `MX` or whose `name` is `@`, before or after, changes. |

`SG-CF-002` and `SG-CF-003` are warnings on purpose: the workflows pass no flags, so a deny could never be lifted
and a legitimate removal could never reach prod. Their risk rules (`cf-access-destroy`, `cf-firewall-destroy`)
make them critical, so the prod reviewer sees them first.

## Data file

Stage and flags come from `data.ayasee.stage` (string) and `data.ayasee.flags` (array of strings). Missing data
means stage `""` (not prod) and no flags. The workflows write the stage (`dev` for pull requests, `prod` for prod-plan and break-glass) and an empty flag list, so `allow-metastore-destroy` can only be set when you run conftest yourself.

```json
{
  "ayasee": {
    "stage": "prod",
    "flags": ["allow-metastore-destroy"]
  }
}
```

## Run locally

```bash
scripts/install-dev-tools.sh opa conftest
tofu show -json plan.tfplan > plan.json
.tools/bin/conftest test --policy policy --namespace ayasee.databricks --namespace ayasee.cloudflare \
  --data ayasee-data.json plan.json
```

`scripts/check-policy-fixture.sh` runs conftest, with the same arguments as the policy action, on
`tests/fixtures/policy/public_workspace.json` (`SG-DBX-003`) and `tests/fixtures/policy/proxy_off.json`
(`SG-CF-001`) and expects a warning without data and a failure with `tests/fixtures/policy/prod.data.json`; CI
runs it too.

Run the unit tests with `.tools/bin/opa test policy/ -v`. CI runs them in the `opa test` job.
