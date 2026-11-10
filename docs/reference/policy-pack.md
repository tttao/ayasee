# Databricks policy pack

Rego rules that run on the plan JSON (`tofu show -json`) inside the PR, prod-plan and break-glass jobs. They live in
`policy/databricks/` under the conftest namespace `ayasee.databricks`.

## Coverage

| Scanner | Databricks checks | Notes |
|---------|-------------------|-------|
| Trivy | 0 | No Databricks checks exist. |
| Checkov | 2 | `CKV_AZURE_158`, `CKV2_AZURE_48` (workspace-level Azure settings). |
| This pack | 6 | `SG-DBX-001` to `SG-DBX-006`, evaluated on the plan, so they see the final `after` values. |

The pack does not replace Trivy or Checkov. It covers the Unity Catalog and job-permission gaps they leave.

## Rules

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
.tools/bin/conftest test --policy policy/databricks --namespace ayasee.databricks \
  --data ayasee-data.json plan.json
```

`scripts/check-policy-fixture.sh` runs conftest on `tests/fixtures/policy/public_workspace.json` and expects a warning without data and a failure with `tests/fixtures/policy/prod.data.json`; CI runs it too.

Run the unit tests with `.tools/bin/opa test policy/ -v`. CI runs them in the `opa test` job.
