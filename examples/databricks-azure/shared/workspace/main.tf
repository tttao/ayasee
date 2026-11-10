terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

# Authentication comes from the environment the workflow sets (ARM_USE_OIDC, ARM_CLIENT_ID,
# ARM_TENANT_ID, ARM_SUBSCRIPTION_ID). Nothing secret is written here.
provider "azurerm" {
  features {}
}

variable "name" {
  description = "Workspace name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the workspace."
  type        = string
}

variable "managed_resource_group_name" {
  description = "Name of the resource group Databricks manages for the workspace."
  type        = string
}

variable "sku" {
  description = "Workspace SKU. Unity Catalog and private networking need premium."
  type        = string
  default     = "premium"
}

variable "public_network_access_enabled" {
  description = "Whether the workspace is reachable from the public internet. Keep false; the policy pack (SG-DBX-003) denies true in prod."
  type        = bool
  default     = false
}

# Private networking: the workspace is injected into your own virtual network, without public IPs.
variable "virtual_network_id" {
  description = "Resource ID of the virtual network that hosts the workspace."
  type        = string
}

variable "public_subnet_name" {
  description = "Name of the host (public) subnet. It has no public IPs."
  type        = string
}

variable "private_subnet_name" {
  description = "Name of the container (private) subnet."
  type        = string
}

variable "public_subnet_nsg_association_id" {
  description = "ID of the network security group association of the host subnet."
  type        = string
}

variable "private_subnet_nsg_association_id" {
  description = "ID of the network security group association of the container subnet."
  type        = string
}

variable "tags" {
  description = "Tags for the workspace."
  type        = map(string)
  default     = {}
}

resource "azurerm_databricks_workspace" "this" {
  name                        = var.name
  location                    = var.location
  resource_group_name         = var.resource_group_name
  managed_resource_group_name = var.managed_resource_group_name
  sku                         = var.sku
  tags                        = var.tags

  public_network_access_enabled = var.public_network_access_enabled
  # With public access off, traffic flows over private endpoints (created by your network layer),
  # so Databricks must not add its own NSG rules.
  network_security_group_rules_required = var.public_network_access_enabled ? "AllRules" : "NoAzureDatabricksRules"

  custom_parameters {
    no_public_ip                                         = true
    virtual_network_id                                   = var.virtual_network_id
    public_subnet_name                                   = var.public_subnet_name
    private_subnet_name                                  = var.private_subnet_name
    public_subnet_network_security_group_association_id  = var.public_subnet_nsg_association_id
    private_subnet_network_security_group_association_id = var.private_subnet_nsg_association_id
  }
}

output "workspace_id" {
  description = "Databricks workspace ID (numeric, as a string)."
  value       = azurerm_databricks_workspace.this.workspace_id
}

output "workspace_url" {
  description = "Workspace host name, without scheme."
  value       = azurerm_databricks_workspace.this.workspace_url
}
