terraform {
  required_version = ">= 1.9.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.0"
    }
  }
}

provider "databricks" {
  host = "https://${var.workspace_url}"
}

variable "workspace_url" {
  description = "Workspace host name without scheme, from the unity-catalog unit."
  type        = string
}

variable "catalog_name" {
  description = "Catalog to grant on, from the unity-catalog unit."
  type        = string
}

variable "principals" {
  description = "Map of principal (group, user or service principal name) to its catalog privileges."
  type        = map(list(string))
}

# One authoritative databricks_grants per catalog: privileges not listed here are revoked.
resource "databricks_grants" "catalog" {
  catalog = var.catalog_name

  dynamic "grant" {
    for_each = var.principals
    content {
      principal  = grant.key
      privileges = grant.value
    }
  }
}
