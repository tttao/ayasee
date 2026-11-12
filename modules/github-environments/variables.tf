variable "repository" {
  description = "Name of the GitHub repository (not owner/name)."
  type        = string
}

variable "stages" {
  description = "Stage names. Each gets an environment and a drift-<stage> environment. Must include prod."
  type        = list(string)
  default     = ["dev", "uat", "prod"]

  validation {
    condition     = contains(var.stages, "prod")
    error_message = "stages must include \"prod\"."
  }
}

variable "prod_reviewers" {
  description = "Required reviewers of the prod environment (GitHub team and user IDs). Must not be empty."
  type = object({
    teams = list(number)
    users = list(number)
  })

  validation {
    condition     = length(var.prod_reviewers.teams) + length(var.prod_reviewers.users) > 0
    error_message = "prod_reviewers must contain at least one team or user (DORA RTS 2024/1774 Art. 17(1)(b))."
  }
}

variable "breakglass_reviewers" {
  description = "Required reviewers of prod-breakglass. Must not be empty and must differ from prod_reviewers."
  type = object({
    teams = list(number)
    users = list(number)
  })

  validation {
    condition     = length(var.breakglass_reviewers.teams) + length(var.breakglass_reviewers.users) > 0
    error_message = "breakglass_reviewers must contain at least one team or user."
  }

  validation {
    condition = !(
      toset(var.breakglass_reviewers.teams) == toset(var.prod_reviewers.teams) &&
      toset(var.breakglass_reviewers.users) == toset(var.prod_reviewers.users)
    )
    error_message = "breakglass_reviewers must differ from prod_reviewers."
  }
}

variable "variables" {
  description = "Environment variables per environment: map of environment name to map of variable name to value (AZURE_CLIENT_ID etc.). No secrets."
  type        = map(map(string))
  default     = {}
}

variable "shared_units_pr_plan" {
  description = "Create prod-pr-plan: the environment of the iac-pr job that plans shared prod units (shared_units in .ayasee.yaml) read-only. It accepts pull-request branches and carries prod_reviewers, so a prod reviewer approves before PR code runs with the read-only prod identity."
  type        = bool
  default     = false
}
