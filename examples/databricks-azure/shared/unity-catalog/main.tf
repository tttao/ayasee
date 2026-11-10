terraform {
  required_version = ">= 1.9.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.0"
    }
  }
}

# Authentication comes from the environment the workflow sets (DATABRICKS_AUTH_TYPE=github-oidc-azure,
# ARM_CLIENT_ID, ARM_TENANT_ID). Nothing secret is written here.
provider "databricks" {
  alias      = "account"
  host       = "https://accounts.azuredatabricks.net"
  account_id = var.databricks_account_id
}

provider "databricks" {
  host = "https://${var.workspace_url}"
}

variable "databricks_account_id" {
  description = "Databricks account ID (a GUID, not a secret)."
  type        = string
}

variable "workspace_id" {
  description = "Numeric workspace ID, from the workspace unit."
  type        = string
}

variable "workspace_url" {
  description = "Workspace host name without scheme, from the workspace unit."
  type        = string
}

variable "metastore_id" {
  description = "ID of the Unity Catalog metastore of the region."
  type        = string
}

variable "access_connector_id" {
  description = "Resource ID of the Databricks access connector (a managed identity with access to the storage)."
  type        = string
}

variable "storage_url" {
  description = "abfss:// URL of the storage the catalog and the external location use."
  type        = string
}

variable "storage_credential_name" {
  description = "Name of the storage credential."
  type        = string
}

variable "external_location_name" {
  description = "Name of the external location."
  type        = string
}

variable "catalog_name" {
  description = "Name of the catalog."
  type        = string
}

variable "isolation_mode" {
  description = "ISOLATED binds the catalog to the workspaces listed in its bindings. The policy pack (SG-DBX-002) denies anything else in prod."
  type        = string
  default     = "ISOLATED"
}

# The workspace joins the metastore. The metastore itself is created once, outside this repository.
resource "databricks_metastore_assignment" "this" {
  provider     = databricks.account
  metastore_id = var.metastore_id
  workspace_id = tonumber(var.workspace_id)
}

resource "databricks_storage_credential" "this" {
  name = var.storage_credential_name

  azure_managed_identity {
    access_connector_id = var.access_connector_id
  }

  depends_on = [databricks_metastore_assignment.this]
}

resource "databricks_external_location" "this" {
  name            = var.external_location_name
  url             = var.storage_url
  credential_name = databricks_storage_credential.this.name
}

resource "databricks_catalog" "this" {
  name           = var.catalog_name
  storage_root   = databricks_external_location.this.url
  isolation_mode = var.isolation_mode
}

# An ISOLATED catalog is visible only to the workspaces it is bound to.
resource "databricks_workspace_binding" "catalog" {
  securable_name = databricks_catalog.this.name
  securable_type = "catalog"
  workspace_id   = tonumber(var.workspace_id)
}

output "catalog_name" {
  description = "Catalog name, for the grants unit."
  value       = databricks_catalog.this.name
}

output "workspace_url" {
  description = "Workspace host name, passed through for the grants unit."
  value       = var.workspace_url
}
