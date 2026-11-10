variable "repository" {
  description = "GitHub repository as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[^/\\s]+/[^/\\s]+$", var.repository))
    error_message = "repository must be \"owner/name\"."
  }
}

variable "environment" {
  description = "GitHub environment the identity is for (dev-plan, drift-prod, prod-plan, prod, prod-breakglass, ...)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the identity."
  type        = string
}

variable "location" {
  description = "Azure region of the identity."
  type        = string
}

variable "issuer" {
  description = "OIDC issuer. Set to the GHES issuer on GitHub Enterprise Server."
  type        = string
  default     = "https://token.actions.githubusercontent.com"
}

variable "subject" {
  description = <<-EOT
    Optional override of the federated credential subject. Default:
    repo:<owner>/<repo>:environment:<environment>. Repositories created, renamed
    or transferred on or after 2026-07-15 get GitHub's immutable `sub` format,
    repo:<org>@<id>/<repo>@<id>:environment:<env>; set this to the exact `sub`
    your workflow tokens carry.
  EOT
  type        = string
  default     = null
}
