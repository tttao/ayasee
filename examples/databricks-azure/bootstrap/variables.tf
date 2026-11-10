variable "github_owner" {
  description = "GitHub organization or user that owns the repository."
  type        = string
}

variable "github_repository" {
  description = "Name of the repository (not owner/name)."
  type        = string
}

variable "tenant_id" {
  description = "Entra tenant ID."
  type        = string
}

variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "resource_group_name" {
  description = "Existing resource group that holds the managed identities."
  type        = string
}

variable "location" {
  description = "Azure region of the managed identities."
  type        = string
}

variable "prod_reviewers" {
  description = "Required reviewers of the prod environment (GitHub team and user IDs)."
  type = object({
    teams = list(number)
    users = list(number)
  })
}

variable "breakglass_reviewers" {
  description = "Required reviewers of prod-breakglass; must differ from prod_reviewers."
  type = object({
    teams = list(number)
    users = list(number)
  })
}

variable "ledger_url" {
  description = "Ledger store URL (README step 3). Empty keeps the url of .ayasee.yaml."
  type        = string
  default     = ""
}

variable "key_vault_key_id" {
  description = "Key Vault key URL for plan encryption (README step 4). Set it before the first prod run."
  type        = string
  default     = ""
}
