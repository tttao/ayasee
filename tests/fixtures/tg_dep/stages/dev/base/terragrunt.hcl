remote_state {
  backend = "local"
  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }
  config = {
    path = "${get_env("AYASEE_FIXTURE_STATE_DIR")}/base.tfstate"
  }
}

inputs = {
  marker = get_env("DEP_MARKER", "v1")
}
