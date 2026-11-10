remote_state {
  backend = "local"
  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }
  config = {
    path = "${get_env("AYASEE_FIXTURE_STATE_DIR")}/app.tfstate"
  }
}

dependencies {
  paths = ["../base"]
}
