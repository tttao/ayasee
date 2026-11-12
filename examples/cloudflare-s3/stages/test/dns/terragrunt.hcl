include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "${dirname(find_in_parent_folders("root.hcl"))}/shared/dns"
}

# Non-secret identifiers are committed: they are not credentials.
inputs = {
  zone_id = "00000000000000000000000000000000" # placeholder: your zone ID
  records = {
    "app-test.example.com" = "00000000-0000-0000-0000-000000000000.cfargotunnel.com"
  }
}
