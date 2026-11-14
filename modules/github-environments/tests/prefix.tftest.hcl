mock_provider "github" {}

variables {
  repository         = "demo"
  environment_prefix = "iac-"
  prod_reviewers = {
    teams = [101]
    users = []
  }
  breakglass_reviewers = {
    teams = [202]
    users = [7]
  }
}

run "every_environment_carries_the_prefix" {
  command = plan

  assert {
    condition = toset(output.environments) == toset([
      "iac-dev", "iac-uat", "iac-prod", "iac-dev-plan", "iac-prod-plan", "iac-prod-breakglass",
      "iac-drift-dev", "iac-drift-uat", "iac-drift-prod",
    ])
    error_message = "every environment name must start with the prefix"
  }
  assert {
    condition     = alltrue([for e in github_repository_environment.this : startswith(e.environment, "iac-")])
    error_message = "no environment may be created without the prefix"
  }
  assert {
    condition     = output.pr_plan_environment == "iac-dev-plan"
    error_message = "the pull-request plan environment is prefixed"
  }
  assert {
    condition     = output.environment_names["prod"] == "iac-prod" && output.environment_names["drift-uat"] == "iac-drift-uat"
    error_message = "environment_names maps the logical name to the GitHub name"
  }
}

run "protection_follows_the_logical_name_not_the_literal" {
  command = plan

  # reviewers and the main-only policy are decided by the logical name: with a prefix, iac-prod
  # must still carry the reviewers and iac-prod-breakglass its own
  assert {
    condition = alltrue([
      for k, e in github_repository_environment.this :
      length(e.reviewers) == (contains(["prod", "prod-breakglass"], k) ? 1 : 0)
    ])
    error_message = "only iac-prod and iac-prod-breakglass carry reviewers"
  }
  assert {
    condition     = github_repository_environment.this["prod"].environment == "iac-prod" && github_repository_environment.this["prod"].prevent_self_review == true
    error_message = "iac-prod prevents self review"
  }
  assert {
    condition     = !contains(keys(github_repository_environment_deployment_policy.main), "dev-plan")
    error_message = "the pull-request plan environment accepts every branch"
  }
  assert {
    condition     = alltrue([for p in github_repository_environment_deployment_policy.main : startswith(p.environment, "iac-") && p.branch_pattern == "main"])
    error_message = "every other environment is restricted to main"
  }
}

run "variables_and_secrets_are_keyed_by_the_logical_name_and_land_on_the_prefixed_environment" {
  command = plan

  variables {
    variables = {
      "prod" = { AZURE_CLIENT_ID = "abc" }
    }
    key_provider = "pbkdf2"
    owner        = "acme"
    secret_scopes = {
      "dev" = { CLOUDFLARE_API_TOKEN = "zone DNS edit" }
    }
  }

  assert {
    condition     = github_actions_environment_variable.this["prod/AZURE_CLIENT_ID"].environment == "iac-prod"
    error_message = "the variable lands on the prefixed environment"
  }
  assert {
    condition     = strcontains(output.secret_commands, "gh secret set CLOUDFLARE_API_TOKEN --env iac-dev --repo acme/demo")
    error_message = "secret_commands name the prefixed environment"
  }
  assert {
    condition     = strcontains(output.secret_commands, "gh secret set AYASEE_PLAN_PASSPHRASE --env iac-prod-breakglass --repo acme/demo")
    error_message = "the plan key goes to the prefixed prod-breakglass"
  }
  assert {
    condition     = !strcontains(output.secret_commands, "--env prod") && !strcontains(output.secret_commands, "--env dev ")
    error_message = "no command may name an unprefixed environment"
  }
}

run "generated_passphrase_lands_on_the_prefixed_environments" {
  command = plan

  variables {
    key_provider             = "pbkdf2"
    generate_plan_passphrase = true
  }

  assert {
    condition     = toset(output.plan_passphrase_environments) == toset(["iac-prod-plan", "iac-prod", "iac-prod-breakglass"])
    error_message = "the passphrase belongs on the three prefixed prod environments"
  }
}

run "shared_units_adds_a_prefixed_prod_pr_plan" {
  command = plan

  variables {
    shared_units_pr_plan = true
  }

  assert {
    condition     = contains(output.environments, "iac-prod-pr-plan")
    error_message = "iac-prod-pr-plan is created"
  }
}

run "prefix_is_validated" {
  command = plan

  variables {
    environment_prefix = "IAC "
  }

  expect_failures = [var.environment_prefix]
}

run "empty_prefix_is_todays_names" {
  command = plan

  variables {
    environment_prefix = ""
  }

  assert {
    condition     = contains(output.environments, "prod") && contains(output.environments, "prod-breakglass") && output.pr_plan_environment == "dev-plan"
    error_message = "no prefix keeps the names of before"
  }
}
