# github-bootstrap Specification

## Purpose

OpenTofu modules applied from an administrator's machine (never from CI) that create the GitHub
environments and the cloud identities the workflows expect: `modules/github-environments` and
`modules/azure-stage-identity`. Reference: their READMEs and `docs/guides/adopt.md`.

## Requirements

### Requirement: The environment set
`github-environments` SHALL create, with `environment_prefix` applied: `<first stage>-plan` (all branches, no reviewers); each stage (`main` only; `prod` with `prod_reviewers`); `prod-plan` (`main` only, no reviewers); `drift-<stage>` per stage (`main` only, no reviewers); `prod-breakglass` (`main` only, `breakglass_reviewers`); and, only with `shared_units_pr_plan = true`, `prod-pr-plan` (all branches with `prod_reviewers`, or `main` only and no reviewers with `gate_mechanism = "workflow"`). Admins SHALL NOT be able to bypass protection.

#### Scenario: Default stages
- **WHEN** the module is applied with `stages = ["dev", "uat", "prod"]`
- **THEN** it creates `dev-plan`, `dev`, `uat`, `prod`, `prod-plan`, `drift-dev`, `drift-uat`, `drift-prod` and `prod-breakglass`

### Requirement: Independent reviewer sets
The module SHALL refuse the same person as prod and break-glass reviewer and SHALL set `prevent_self_review` on gated environments, unless its own `solo_mode = true`, which lifts both while reviewers stay required.

#### Scenario: Overlapping reviewers
- **WHEN** `prod_reviewers` and `breakglass_reviewers` name the same user without `solo_mode`
- **THEN** the plan fails

### Requirement: Refuse settings the GitHub plan cannot hold
The module SHALL read the repository visibility; a private repository SHALL state `private_repository_plan` (`free`, `pro`, `team`, `enterprise`), and the plan SHALL fail on every environment whose reviewers or `main`-only policy that plan cannot hold, naming the environment; a postcondition SHALL fail when GitHub did not keep the reviewers of `prod`, `prod-breakglass` or `prod-pr-plan`. With `gate_mechanism = "workflow"` it SHALL refuse reviewer variables, and on Free it SHALL refuse `shared_units_pr_plan`.

#### Scenario: Private Pro repository with the environment gate
- **WHEN** `private_repository_plan = "pro"` and the default gate are applied
- **THEN** the plan fails on `prod` because Pro cannot hold required reviewers

### Requirement: Never take over an existing environment silently
The module SHALL mark its environments with an `AYASEE_MANAGED` variable and refuse to plan when an environment it would create exists without the marker, naming it, unless `adopt_existing_environments = true` is applied once.

#### Scenario: Application prod environment exists
- **WHEN** an unmarked `prod` environment already exists
- **THEN** the plan fails naming `prod`

### Requirement: Variables, generated passphrase and secret commands
The module SHALL set `AYASEE_LEDGER_URL` where the ledger is read or written, optionally generate `AYASEE_PLAN_PASSPHRASE` on `prod-plan`, `prod` and `prod-breakglass` (`generate_plan_passphrase = true`), and output `secret_commands`: one `gh secret set` command per secret and environment it could not create, with the needed scope as a comment and never a value.

#### Scenario: Remaining secrets
- **WHEN** `tofu output -raw secret_commands` is run after apply
- **THEN** it lists commands without values that prompt for each secret

### Requirement: One identity per environment
`azure-stage-identity`, called once per GitHub environment, SHALL create one user-assigned managed identity named `ayasee-<repo>-<environment>` and one federated credential (audience `api://AzureADTokenExchange`) whose subject is `repo:<owner>/<repo>:environment:<prefixed name>` (or the exact immutable-subject `sub` passed through its `subject` input), so that the boundary is the environment; identities SHALL never be shared between environments, in particular not between `prod` and `prod-breakglass`.

#### Scenario: Prefixed environment
- **WHEN** `environment_prefix` is `iac-`
- **THEN** the prod identity's subject ends with `environment:iac-prod`
