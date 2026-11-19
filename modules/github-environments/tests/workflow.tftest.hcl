# gate_mechanism = "workflow" (D15, issue #40): ayasee's iac-approve holds the prod gate, so no
# environment carries reviewers. The main-only branch policies stay where the plan has them; a
# private repository on GitHub Free, which has none, gets bare environments and repository secrets.
mock_provider "github" {
  mock_data "github_repository" {
    defaults = { visibility = "private" }
  }
}

variables {
  repository     = "demo"
  stages         = ["test", "prod"]
  gate_mechanism = "workflow"
  key_provider   = "pbkdf2"
  secret_scopes = {
    test = { CLOUDFLARE_API_TOKEN = "test zone edit" }
    prod = { CLOUDFLARE_API_TOKEN = "prod zone edit" }
  }
}

run "a_private_repository_on_team_keeps_main_only_environments" {
  command = plan

  variables {
    private_repository_plan = "team"
  }

  assert {
    condition     = length(github_repository_environment.this["prod"].reviewers) == 0 && length(github_repository_environment.this["prod-breakglass"].reviewers) == 0
    error_message = "the workflow gate sets no reviewers"
  }

  assert {
    condition     = length(github_repository_environment.this["prod"].deployment_branch_policy) == 1
    error_message = "prod stays restricted to main"
  }

  assert {
    condition     = github_repository_environment_deployment_policy.main["prod"].branch_pattern == "main"
    error_message = "prod deploys from main"
  }

  # break-glass of the workflow gate applies in prod-breakglass: main only, like prod
  assert {
    condition     = length(github_repository_environment.this["prod-breakglass"].deployment_branch_policy) == 1 && github_repository_environment_deployment_policy.main["prod-breakglass"].branch_pattern == "main"
    error_message = "prod-breakglass stays restricted to main"
  }

  assert {
    condition     = strcontains(output.secret_commands, "gh secret set AYASEE_PLAN_PASSPHRASE --env prod-breakglass")
    error_message = "the break-glass apply holds the plan key"
  }

  assert {
    condition     = strcontains(output.secret_commands, "gh secret set AYASEE_PLAN_PASSPHRASE --env prod")
    error_message = "the plan key stays an environment secret where the plan has them"
  }

  assert {
    condition     = length(github_actions_environment_variable.marker) == length(github_repository_environment.this)
    error_message = "every environment is marked"
  }
}

run "a_private_repository_on_free_gets_bare_environments" {
  command = plan

  variables {
    private_repository_plan = "free"
  }

  assert {
    condition     = length(github_repository_environment.this["prod"].deployment_branch_policy) == 0
    error_message = "GitHub Free has no deployment branch policies"
  }

  assert {
    condition     = length(github_repository_environment_deployment_policy.main) == 0 && length(github_actions_environment_variable.marker) == 0
    error_message = "no branch policy and no environment variable on GitHub Free"
  }

  assert {
    condition     = !strcontains(output.secret_commands, "--env")
    error_message = "GitHub Free has no environment secrets: every secret is a repository secret"
  }

  assert {
    condition     = length(regexall("gh secret set CLOUDFLARE_API_TOKEN", output.secret_commands)) == 1
    error_message = "one repository secret per name"
  }
}

run "environment_variables_are_refused_on_free" {
  command = plan

  variables {
    private_repository_plan = "free"
    variables               = { prod = { AYASEE_LEDGER_URL = "x" } }
  }

  expect_failures = [
    github_repository_environment.this,
  ]
}

run "an_unstated_private_plan_is_still_refused" {
  command = plan

  expect_failures = [
    github_repository_environment.this,
  ]
}

run "reviewers_are_refused_with_the_workflow_gate" {
  command = plan

  variables {
    private_repository_plan = "team"
    prod_reviewers = {
      teams = [101]
      users = []
    }
  }

  expect_failures = [
    var.prod_reviewers,
  ]
}

# iac-pr-shared plans the shared units of a pull request from main: prod-pr-plan carries no
# reviewer, so only its main-only branch policy keeps other branches from its credentials
run "the_workflow_gate_keeps_prod_pr_plan_main_only" {
  command = plan

  variables {
    private_repository_plan = "team"
    shared_units_pr_plan    = true
  }

  assert {
    condition     = length(github_repository_environment.this["prod-pr-plan"].reviewers) == 0
    error_message = "the workflow gate sets no reviewers on prod-pr-plan"
  }

  assert {
    condition     = length(github_repository_environment.this["prod-pr-plan"].deployment_branch_policy) == 1
    error_message = "prod-pr-plan is restricted to main with the workflow gate"
  }

  assert {
    condition     = github_repository_environment_deployment_policy.main["prod-pr-plan"].branch_pattern == "main"
    error_message = "prod-pr-plan deploys from main only"
  }

  assert {
    condition     = output.pr_plan_environment == "test-plan" && length(github_repository_environment.this["test-plan"].deployment_branch_policy) == 0
    error_message = "the first stage's pull-request plan environment still accepts every branch"
  }
}

# GitHub Free has no branch policy to close prod-pr-plan to other branches, and no environment
# secret to keep its credentials apart from the prod apply's
run "no_prod_pr_plan_with_the_workflow_gate_on_free" {
  command = plan

  variables {
    private_repository_plan = "free"
    shared_units_pr_plan    = true
  }

  expect_failures = [
    github_repository_environment.this,
  ]
}

run "the_environment_gate_still_needs_reviewers" {
  command = plan

  variables {
    gate_mechanism          = "environment"
    private_repository_plan = "enterprise"
  }

  expect_failures = [
    var.prod_reviewers,
  ]
}
