mock_provider "github" {}

variables {
  repository = "demo"
  prod_reviewers = {
    teams = [101]
    users = []
  }
  breakglass_reviewers = {
    teams = [202]
    users = [7]
  }
}

run "prod_is_independent" {
  command = plan

  assert {
    condition     = github_repository_environment.this["prod"].prevent_self_review == true
    error_message = "prod must prevent self review"
  }
  assert {
    condition     = github_repository_environment.this["prod"].can_admins_bypass == false
    error_message = "prod must not allow admin bypass"
  }
}

run "all_environments_exist" {
  command = plan

  assert {
    condition = toset(output.environments) == toset([
      "dev", "uat", "prod", "dev-plan", "prod-plan", "prod-breakglass",
      "drift-dev", "drift-uat", "drift-prod",
    ])
    error_message = "unexpected environment set"
  }
}

run "no_environment_allows_self_review_or_admin_bypass" {
  command = plan

  assert {
    condition = alltrue([
      for e in github_repository_environment.this :
      e.prevent_self_review == true && e.can_admins_bypass == false
    ])
    error_message = "every environment must prevent self review and deny admin bypass"
  }
}

run "reviewers_only_on_prod_and_breakglass" {
  command = plan

  assert {
    condition = alltrue([
      for k, e in github_repository_environment.this :
      length(e.reviewers) == (contains(["prod", "prod-breakglass"], k) ? 1 : 0)
    ])
    error_message = "only prod and prod-breakglass carry reviewers"
  }
  assert {
    condition     = one(github_repository_environment.this["prod-breakglass"].reviewers).teams == toset([202])
    error_message = "breakglass reviewers must be its own"
  }
  assert {
    condition     = one(github_repository_environment.this["prod"].reviewers).teams == toset([101])
    error_message = "prod reviewers must be its own"
  }
}

run "dev_plan_has_no_branch_policy" {
  command = plan

  assert {
    condition     = length(github_repository_environment.this["dev-plan"].deployment_branch_policy) == 0
    error_message = "dev-plan must allow all branches"
  }
  assert {
    condition     = !contains(keys(github_repository_environment_deployment_policy.main), "dev-plan")
    error_message = "dev-plan must have no main policy"
  }
}

run "every_other_environment_is_main_only" {
  command = plan

  assert {
    condition = alltrue([
      for k, e in github_repository_environment.this :
      k == "dev-plan" || (
        one(e.deployment_branch_policy).custom_branch_policies == true &&
        one(e.deployment_branch_policy).protected_branches == false &&
        github_repository_environment_deployment_policy.main[k].branch_pattern == "main"
      )
    ])
    error_message = "all environments except dev-plan need a custom main-only branch policy"
  }
}

run "drift_environment_per_stage" {
  command = plan
  variables {
    stages = ["dev", "qa", "prod"]
  }

  assert {
    condition = alltrue([
      for s in ["dev", "qa", "prod"] :
      contains(keys(github_repository_environment.this), "drift-${s}")
    ])
    error_message = "drift-<stage> must exist for each stage"
  }
}

run "variables_are_written" {
  command = plan
  variables {
    variables = {
      "prod-plan" = { AZURE_CLIENT_ID = "abc" }
      "dev"       = { AZURE_CLIENT_ID = "def", AZURE_TENANT_ID = "t" }
    }
  }

  assert {
    condition     = length(github_actions_environment_variable.this) == 3
    error_message = "one variable resource per env/name"
  }
  assert {
    condition     = github_actions_environment_variable.this["prod-plan/AZURE_CLIENT_ID"].value == "abc"
    error_message = "variable value not propagated"
  }
}

run "breakglass_equal_to_prod_fails" {
  command = plan
  variables {
    breakglass_reviewers = {
      users = []
      teams = [101]
    }
  }
  expect_failures = [var.breakglass_reviewers]
}

run "empty_prod_reviewers_fails" {
  command = plan
  variables {
    prod_reviewers = {
      teams = []
      users = []
    }
  }
  expect_failures = [var.prod_reviewers]
}

run "empty_breakglass_reviewers_fails" {
  command = plan
  variables {
    breakglass_reviewers = {
      teams = []
      users = []
    }
  }
  expect_failures = [var.breakglass_reviewers]
}

run "prod_pr_plan_is_opt_in" {
  command = plan

  assert {
    condition     = !contains(keys(github_repository_environment.this), "prod-pr-plan")
    error_message = "prod-pr-plan must not exist unless shared_units_pr_plan is true"
  }
}

run "prod_pr_plan_accepts_pr_branches_and_needs_prod_reviewers" {
  command = plan
  variables {
    shared_units_pr_plan = true
  }

  assert {
    condition     = length(github_repository_environment.this["prod-pr-plan"].deployment_branch_policy) == 0
    error_message = "prod-pr-plan must accept pull-request branches"
  }
  assert {
    condition     = !contains(keys(github_repository_environment_deployment_policy.main), "prod-pr-plan")
    error_message = "prod-pr-plan must have no main policy"
  }
  assert {
    condition     = one(github_repository_environment.this["prod-pr-plan"].reviewers).teams == toset([101])
    error_message = "prod-pr-plan must carry the prod reviewers"
  }
  assert {
    condition     = github_repository_environment.this["prod-pr-plan"].prevent_self_review == true
    error_message = "prod-pr-plan must prevent self review"
  }
  assert {
    condition     = github_repository_environment_deployment_policy.main["prod-plan"].branch_pattern == "main"
    error_message = "prod-plan stays main-only"
  }
}

run "solo_mode_allows_one_reviewer_set_and_self_review_on_prod_side_only" {
  command = plan
  variables {
    solo_mode            = true
    shared_units_pr_plan = true
    prod_reviewers       = { teams = [], users = [7] }
    breakglass_reviewers = { teams = [], users = [7] }
  }

  assert {
    condition = alltrue([
      for k in ["prod", "prod-breakglass", "prod-pr-plan"] :
      github_repository_environment.this[k].prevent_self_review == false
    ])
    error_message = "solo_mode lets the one maintainer approve prod, break-glass and prod-pr-plan"
  }
  assert {
    condition = alltrue([
      for k, e in github_repository_environment.this :
      e.can_admins_bypass == false
    ])
    error_message = "solo_mode never allows admin bypass"
  }
  assert {
    condition     = one(github_repository_environment.this["prod"].reviewers).users == toset([7])
    error_message = "prod still requires the reviewer"
  }
}

run "without_solo_mode_equal_reviewer_sets_still_fail" {
  command = plan
  variables {
    prod_reviewers       = { teams = [], users = [7] }
    breakglass_reviewers = { teams = [], users = [7] }
  }
  expect_failures = [var.breakglass_reviewers]
}
