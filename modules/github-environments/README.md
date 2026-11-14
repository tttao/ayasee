# github-environments

Creates the GitHub environments the ayasee workflows expect, plus their environment variables.
Providers: `integrations/github` (`~> 6.12`), `hashicorp/random`.

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

## Secrets

The module creates no secret by default. It lists those it cannot create, for a person to set:
`secret_commands` is one `gh secret set` command per secret, with the scope its value needs as a
comment and never a value (print it with `tofu output -raw`; run the lines one at a time, since
`gh` prompts for each value). The list is:

- the repository secrets of `repository_secret_scopes` (name to scope: `AYASEE_LEDGER_TOKEN`,
  `TEAMS_WEBHOOK`);
- the environment secrets of `secret_scopes` (environment to name to scope: the provider tokens of
  `identity: secrets`); an environment the module does not create fails the plan;
- the plan-key secrets of `prod-plan`, `prod` and `prod-breakglass`, from `key_provider`
  (`plan_encryption.key_provider`): `AYASEE_PLAN_PASSPHRASE` for `pbkdf2`, `AYASEE_KMS_ACCESS_KEY_ID`
  and `AYASEE_KMS_SECRET_ACCESS_KEY` for `aws_kms`, `AYASEE_GCP_KMS_CREDENTIALS` for `gcp_kms`,
  `AYASEE_OPENBAO_TOKEN` for `openbao`, none for `azure_vault` (the default).

These are the names `ayasee doctor` checks. `owner` adds `--repo <owner>/<repository>` to each
command. `pending_secrets` is the same list as data.

`generate_plan_passphrase` (default `false`, `key_provider = "pbkdf2"` only) generates
`AYASEE_PLAN_PASSPHRASE`, 64 random characters of `[A-Za-z0-9]`, and sets it on `prod-plan`, `prod`
and `prod-breakglass`. The github provider has no write-only secret argument, so the value is
kept in plain text in the state of the calling root: whoever reads that state can decrypt the
approved prod plans, which is why it is opt-in. Turning it on replaces a passphrase set by hand, and
turning it off deletes the secret; a plan encrypted with the old passphrase can then no longer be
applied, so do either when no approved prod plan is waiting.

Outputs: `environments`, the list of environment names, `pr_plan_environment`, `secret_commands`,
`pending_secrets` and `plan_passphrase_environments`. Test: `tofu init -backend=false && tofu test`.
