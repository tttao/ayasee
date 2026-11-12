# github-environments

Creates the GitHub environments the ayasee workflows expect, plus their environment variables.
Provider: `integrations/github`.

| Environment | Branches | Reviewers |
|---|---|---|
| `dev-plan` | all | none |
| each stage (default `dev`, `uat`, `prod`) | `main` only | none (`prod`: `prod_reviewers`) |
| `prod-plan` | `main` only | none |
| `drift-<stage>` per stage | `main` only | none |
| `prod-breakglass` | `main` only | `breakglass_reviewers` |
| `prod-pr-plan` (only with `shared_units_pr_plan = true`) | all | `prod_reviewers` |

Every environment sets `prevent_self_review = true` and `can_admins_bypass = false` (D2).
`prod_reviewers` and `breakglass_reviewers` must be non-empty, and the break-glass set must differ
from the prod set.

`shared_units_pr_plan` (default `false`) adds `prod-pr-plan`, where `iac-pr` plans the shared prod
units of a pull request read-only ([shared units](../../docs/concepts/pr-checks.md#shared-units)).

```hcl
module "environments" {
  source     = "./modules/github-environments"
  repository = "my-repo"
  prod_reviewers       = { teams = [101], users = [] }
  breakglass_reviewers = { teams = [202], users = [] }
  variables = {
    "prod-plan" = { AZURE_CLIENT_ID = module.id_prod_plan.client_id }
  }
}
```

Output: `environments`, the list of environment names. Test: `tofu init -backend=false && tofu test`.
