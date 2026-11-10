include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "${dirname(find_in_parent_folders("root.hcl"))}/shared/grants"
}

# The catalog name comes from the unity-catalog unit (R52 applies, see that unit).
dependency "unity_catalog" {
  config_path = "../unity-catalog"

  mock_outputs = {
    catalog_name  = "mock_catalog"
    workspace_url = "adb-1234567890123456.7.azuredatabricks.net"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

inputs = {
  workspace_url = dependency.unity_catalog.outputs.workspace_url
  catalog_name  = dependency.unity_catalog.outputs.catalog_name

  principals = {
    "data-engineers" = ["USE_CATALOG", "USE_SCHEMA", "SELECT", "MODIFY"]
    "data-analysts"  = ["USE_CATALOG", "USE_SCHEMA", "SELECT"]
  }
}
