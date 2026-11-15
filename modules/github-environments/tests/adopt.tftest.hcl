# An environment that already exists and was not created by this module (an application's own
# test or prod) is refused unless the adopter says so (issue #31).
mock_provider "github" {
  # a public repository: every environment feature is available (issue #39)
  mock_data "github_repository" {
    defaults = { visibility = "public" }
  }
  mock_data "github_repository_environments" {
    defaults = {
      environments = [
        { name = "prod", node_id = "n1" },
        { name = "Test", node_id = "n2" },
      ]
    }
  }
  mock_data "github_actions_environment_variables" {
    defaults = {
      variables = [{ name = "DEPLOY_HOST", value = "x", created_at = "", updated_at = "" }]
    }
  }
}

variables {
  repository = "demo"
  stages     = ["test", "prod"]
  prod_reviewers = {
    teams = [101]
    users = []
  }
  breakglass_reviewers = {
    teams = [202]
    users = [7]
  }
}

run "an_existing_foreign_prod_is_refused" {
  command = plan

  expect_failures = [
    github_repository_environment.this["prod"],
    github_repository_environment.this["test"],
  ]
}

run "the_message_names_the_environment_and_the_way_out" {
  command = plan

  # a prefix gives ayasee environments of its own: nothing collides
  variables {
    environment_prefix = "iac-"
  }

  assert {
    condition     = contains(output.environments, "iac-prod") && contains(output.environments, "iac-test")
    error_message = "a prefix avoids the collision"
  }
}

run "adopting_is_possible_when_asked_for" {
  command = plan

  variables {
    adopt_existing_environments = true
  }

  assert {
    condition     = contains(output.environments, "prod") && contains(output.environments, "test")
    error_message = "adoption plans the environments"
  }
  assert {
    condition     = github_actions_environment_variable.marker["prod"].variable_name == "AYASEE_MANAGED"
    error_message = "adoption writes the marker, so that later plans pass without the flag"
  }
}
