include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "${dirname(find_in_parent_folders("root.hcl"))}/shared/tunnel"
}

# Shared by every stage (shared_units in .ayasee.yaml): the one tunnel of the host carries the
# test and the prod hostnames, so a test-only change is still a change to an object that carries
# prod traffic. It is applied behind the prod gate and planned read-only in pull requests.
inputs = {
  account_id = "00000000000000000000000000000000"     # placeholder: your account ID
  tunnel_id  = "00000000-0000-0000-0000-000000000000" # placeholder: the existing tunnel
  ingress = {
    "app-test.example.com" = "http://localhost:8081"
    "app.example.com"      = "http://localhost:8080"
  }
}
