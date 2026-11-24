## MODIFIED Requirements

### Requirement: Refuse settings the GitHub plan cannot hold
The module SHALL read the repository visibility; a private repository SHALL state `private_repository_plan` (`free`, `pro`, `team`, `enterprise`), and the plan SHALL fail on every environment whose reviewers or `main`-only policy that plan cannot hold, naming the environment; a postcondition SHALL fail when GitHub did not keep the reviewers of `prod`, `prod-breakglass` or `prod-pr-plan`. With `gate_mechanism = "workflow"` it SHALL refuse reviewer variables, and on Free it SHALL refuse `shared_units_pr_plan`. With `gate_mechanism = "environment"` the plan SHALL fail whenever `prod`, `prod-breakglass` or `prod-pr-plan` would be created without required reviewers.

#### Scenario: Private Pro repository with the environment gate
- **WHEN** `private_repository_plan = "pro"` and the default gate are applied
- **THEN** the plan fails on `prod` because Pro cannot hold required reviewers

#### Scenario: Environment gate without reviewers
- **WHEN** the default gate is applied with `prod_reviewers` unset
- **THEN** the plan fails and no environment is created without its reviewers

## ADDED Requirements

### Requirement: What init writes passes Trivy
`github-environments` SHALL represent "no reviewers" as an empty reviewer set, never as null, so that no `for_each` local holds a null object, without changing the environments, reviewers or branch policies it plans. `trivy config`, at the Trivy version the workflows use, SHALL complete without a crash on the bootstrap `ayasee init` writes for each profile and gate, including `gate_mechanism = "workflow"` on GitHub Free with `prod_reviewers` and `breakglass_reviewers` unset; ayasee's CI SHALL check this over several runs, with the module sources pointed at the checkout under test.

#### Scenario: Workflow gate on GitHub Free
- **WHEN** `trivy config` scans the bootstrap of `ayasee init --profile cloudflare-s3 --plan free` repeatedly
- **THEN** every run exits without a crash, and the module plans no reviewer on any environment
