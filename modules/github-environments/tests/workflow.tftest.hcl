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

run "no_prod_pr_plan_with_the_workflow_gate" {
  command = plan

  variables {
    private_repository_plan = "team"
    shared_units_pr_plan    = true
  }

  expect_failures = [
    var.shared_units_pr_plan,
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
