# Root Terragrunt configuration, included by every unit.
#
# Layout: stages/<stage>/<unit>/terragrunt.hcl. The OpenTofu code of each unit lives once in
# shared/<unit> and is selected by the unit's `terraform.source`.
locals {
  # stages/<stage>/<unit>
  stage = basename(dirname(get_terragrunt_dir()))
  unit  = basename(get_terragrunt_dir())

  # Replace the placeholders with your state storage, or export the variables.
  state_resource_group  = get_env("AYASEE_STATE_RESOURCE_GROUP", "rg-ayasee-state")
  state_storage_account = get_env("AYASEE_STATE_STORAGE_ACCOUNT", "stgayaseestate")
}

remote_state {
  backend = "azurerm"

  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }

  config = {
    # Sign in with the workflow's OIDC token and authorise against the data plane with Entra ID:
    # no storage account key and no SAS token exist anywhere.
    use_oidc         = true
    use_azuread_auth = true

    resource_group_name  = local.state_resource_group
    storage_account_name = local.state_storage_account
    # One container per stage: each environment's identity only gets a role on its own container.
    container_name = "tfstate-${local.stage}"
    key            = "${local.stage}/${local.unit}.tfstate"
  }
}
