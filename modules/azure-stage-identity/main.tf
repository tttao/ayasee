terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

locals {
  # Azure identity and credential names allow letters, digits, - and _ only: "my.repo" -> "my-repo"
  repo_name = replace(split("/", var.repository)[1], "/[^A-Za-z0-9_-]/", "-")
  subject   = coalesce(var.subject, "repo:${var.repository}:environment:${var.environment}")
}

resource "azurerm_user_assigned_identity" "this" {
  name                = "ayasee-${local.repo_name}-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
}

resource "azurerm_federated_identity_credential" "this" {
  name                      = "ayasee-${local.repo_name}-${var.environment}"
  user_assigned_identity_id = azurerm_user_assigned_identity.this.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = var.issuer
  subject                   = local.subject
}
