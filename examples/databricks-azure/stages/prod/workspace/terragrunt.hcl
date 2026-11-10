include "root" {
  path = find_in_parent_folders("root.hcl")
}

terraform {
  source = "${dirname(find_in_parent_folders("root.hcl"))}/shared/workspace"
}

inputs = {
  name                        = "dbw-example-prod"
  location                    = "westeurope"
  resource_group_name         = "rg-example-prod"
  managed_resource_group_name = "rg-example-prod-dbw-managed"

  # Private networking: replace with your virtual network and subnets.
  virtual_network_id                = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-example-prod/providers/Microsoft.Network/virtualNetworks/vnet-example-prod"
  public_subnet_name                = "snet-dbw-host"
  private_subnet_name               = "snet-dbw-container"
  public_subnet_nsg_association_id  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-example-prod/providers/Microsoft.Network/virtualNetworks/vnet-example-prod/subnets/snet-dbw-host"
  private_subnet_nsg_association_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-example-prod/providers/Microsoft.Network/virtualNetworks/vnet-example-prod/subnets/snet-dbw-container"

  # The policy pack (SG-DBX-003) denies true in prod.
  public_network_access_enabled = false
}
