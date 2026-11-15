# What the repository's GitHub plan allows (issue #39). A private repository has required
# reviewers on environments on GitHub Enterprise only, and deployment branch policies on GitHub
# Pro, Team or Enterprise: on any other plan the module refuses to create an environment whose
# reviewers or branch policy GitHub would not keep, instead of creating it unprotected.
mock_provider "github" {
  mock_data "github_repository" {
    defaults = { visibility = "private" }
  }
}

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

run "a_private_repository_of_an_unstated_plan_is_refused" {
  command = plan

  expect_failures = [
    github_repository_environment.this,
  ]
}

run "a_private_repository_on_team_is_refused" {
  command = plan

  variables {
    private_repository_plan = "team"
  }

  expect_failures = [
    github_repository_environment.this,
  ]
}

run "a_private_repository_on_free_is_refused" {
  command = plan

  variables {
    private_repository_plan = "free"
  }

  expect_failures = [
    github_repository_environment.this,
  ]
}

run "a_private_repository_on_enterprise_is_planned" {
  command = plan

  variables {
    private_repository_plan = "enterprise"
  }

  assert {
    condition     = length(github_repository_environment.this["prod"].reviewers) == 1
    error_message = "prod keeps its reviewers"
  }
}

run "an_unknown_plan_name_is_refused" {
  command = plan

  variables {
    private_repository_plan = "business"
  }

  expect_failures = [
    var.private_repository_plan,
  ]
}
