# An existing environment that carries the AYASEE_MANAGED variable is ours: re-applying is fine.
mock_provider "github" {
  mock_data "github_repository_environments" {
    defaults = {
      environments = [{ name = "prod", node_id = "n1" }, { name = "dev", node_id = "n2" }]
    }
  }
  mock_data "github_actions_environment_variables" {
    defaults = {
      variables = [{ name = "AYASEE_MANAGED", value = "true", created_at = "", updated_at = "" }]
    }
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

run "environments_this_module_created_are_not_refused" {
  command = plan

  assert {
    condition     = contains(output.environments, "prod") && contains(output.environments, "dev")
    error_message = "a marked environment is managed here, not foreign"
  }
}
