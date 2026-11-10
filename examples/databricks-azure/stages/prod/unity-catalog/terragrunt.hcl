include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "${dirname(find_in_parent_folders("root.hcl"))}/shared/unity-catalog"
}

# The workspace id and url come from the workspace unit. A plan of this unit is only valid for the
# state of that unit it was made against, so a merge that changes both is refused at prod-plan (R52).
dependency "workspace" {
  config_path = "../workspace"

  mock_outputs = {
    workspace_id  = "1234567890123456"
    workspace_url = "adb-1234567890123456.7.azuredatabricks.net"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

inputs = {
  workspace_id  = dependency.workspace.outputs.workspace_id
  workspace_url = dependency.workspace.outputs.workspace_url

  databricks_account_id   = "00000000-0000-0000-0000-000000000000"
  metastore_id            = "00000000-0000-0000-0000-000000000000"
  access_connector_id     = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-example-prod/providers/Microsoft.Databricks/accessConnectors/ac-example-prod"
  storage_url             = "abfss://uc@stexampleprod.dfs.core.windows.net/"
  storage_credential_name = "example-prod"
  external_location_name  = "example-prod"
  catalog_name            = "example_prod"
  isolation_mode          = "ISOLATED"
}
