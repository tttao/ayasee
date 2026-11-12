# Root Terragrunt configuration, included by every unit.
#
# Layout: stages/<stage>/<unit>/terragrunt.hcl. The OpenTofu code of each unit lives once in
# shared/<unit> and is selected by the unit's `terraform.source`. State is in an S3-compatible
# bucket per stage (docs/guides/quickstart.md, "Without Azure").
locals {
  # stages/<stage>/<unit>
  stage = basename(dirname(get_terragrunt_dir()))
  unit  = basename(get_terragrunt_dir())

  # Replace the placeholders with your store, or export the variables.
  state_bucket_prefix = get_env("AYASEE_STATE_BUCKET_PREFIX", "ayasee-state")
  state_endpoint      = get_env("AYASEE_STATE_ENDPOINT", "https://s3.example.invalid")
  state_region        = get_env("AYASEE_STATE_REGION", "us-east-1")
}

remote_state {
  backend = "s3"

  # the buckets exist already (created once, by hand); never create them here
  disable_init = true

  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }

  config = {
    bucket       = "${local.state_bucket_prefix}-${local.stage}"
    key          = "${local.stage}/${local.unit}.tfstate"
    region       = local.state_region
    use_lockfile = true # needs OpenTofu 1.10 and a store with conditional writes

    endpoints = {
      s3 = local.state_endpoint
    }
    use_path_style              = true
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
  }
}
