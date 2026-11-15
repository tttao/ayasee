# A public or an internal repository has every environment feature, whatever the plan; a
# visibility the module does not know is refused, never taken for one that has them (issue #39).
mock_provider "github" {
  mock_data "github_repository" {
    defaults = { visibility = "internal" }
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

run "an_internal_repository_is_planned" {
  command = plan

  assert {
    condition     = length(github_repository_environment.this["prod"].reviewers) == 1
    error_message = "prod keeps its reviewers"
  }
}

run "an_unknown_visibility_is_refused" {
  command = plan

  override_data {
    target = data.github_repository.this
    values = { visibility = "something-new" }
  }

  expect_failures = [
    github_repository_environment.this,
  ]
}
