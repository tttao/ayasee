include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "${dirname(find_in_parent_folders("root.hcl"))}/shared/access"
}

# Shared by every stage: who may reach the host that runs test and prod.
inputs = {
  account_id     = "00000000000000000000000000000000" # placeholder: your account ID
  ssh_hostname   = "ssh.example.com"
  allowed_emails = ["ops@example.com"]
}
