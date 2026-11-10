remote_state {
  backend = "local"
  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }
  config = {
    path = "${get_env("AYASEE_FIXTURE_STATE_DIR")}/grants.tfstate"
  }
}

# Reads an output of base: the cascade case (R52).
dependency "base" {
  config_path = "../base"

  mock_outputs = {
    marker = "mock"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

inputs = {
  upstream = dependency.base.outputs.marker
  tag      = get_env("GRANTS_TAG", "g1")
}
