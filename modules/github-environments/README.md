# github-environments

Creates the GitHub environments the ayasee workflows expect, plus their environment variables.
Provider: `integrations/github`.

| Environment | Branches | Reviewers |
|---|---|---|
| `<first stage>-plan` (`dev-plan` by default) | all | none |
| each stage (default `dev`, `uat`, `prod`) | `main` only | none (`prod`: `prod_reviewers`) |
| `prod-plan` | `main` only | none |
| `drift-<stage>` per stage | `main` only | none |
| `prod-breakglass` | `main` only | `breakglass_reviewers` |
| `prod-pr-plan` (only with `shared_units_pr_plan = true`) | all | `prod_reviewers` |

The pull-request plan environment is named after the first stage, so `stages = ["test", "prod"]`
gives `test-plan`, matching `prod-plan` (output `pr_plan_environment`). The first stage must not be
`prod`, and no stage may end in `-plan`, start with `drift-` or be `prod-pr` or `prod-breakglass`:
those names would make a pull-request environment, which accepts every branch, collide with one
that has a fixed role.

Every environment sets `prevent_self_review = true` and `can_admins_bypass = false` (D2).
`prod_reviewers` and `breakglass_reviewers` must be non-empty, and the break-glass set must differ
from the prod set.

`solo_mode` (default `false`) is the bootstrap side of `solo_mode: true` in `.ayasee.yaml`, and
is **NON-COMPLIANT** in the same way: it allows `prod_reviewers` and `breakglass_reviewers` to be the
same person and turns `prevent_self_review` off on `prod`, `prod-breakglass` and `prod-pr-plan`, so
that person can approve their own change. Reviewers are still required and admins still cannot
bypass, so every prod apply still waits for a deliberate approval.

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

Outputs: `environments`, the list of environment names, and `pr_plan_environment`. Test: `tofu init -backend=false && tofu test`.
