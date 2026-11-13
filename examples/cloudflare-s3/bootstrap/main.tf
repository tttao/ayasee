# The GitHub environments of the Cloudflare and S3 reference repository. There is no identity
# module: with identity: secrets, each environment holds provider tokens (README, step 3).
terraform {
  required_version = ">= 1.10.0"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.6"
    }
  }
}

variable "github_owner" {
  type = string
}

variable "github_repository" {
  type = string
}

variable "prod_reviewers" {
  description = "GitHub team and user IDs that approve prod (and prod-pr-plan)."
  type        = object({ teams = list(number), users = list(number) })
}

variable "breakglass_reviewers" {
  description = "GitHub team and user IDs that approve prod-breakglass; must differ from prod_reviewers."
  type        = object({ teams = list(number), users = list(number) })
}

variable "repository_path" {
  description = "Checkout path of the repository on the runner, for the git-branch:// ledger."
  type        = string
  default     = ""
}

provider "github" {
  owner = var.github_owner
}

locals {
  # the stages of .ayasee.yaml, in order; everything below is derived from them
  stages     = ["test", "prod"]
  workspace  = var.repository_path != "" ? var.repository_path : "/home/runner/work/${var.github_repository}/${var.github_repository}"
  ledger_url = "git-branch://${local.workspace}?branch=ayasee-ledger&remote=origin"
  # every environment that writes or reads the ledger: the stages, prod-plan, prod-breakglass and
  # the drift environments (not the pull-request plan ones)
  ledger_envs = concat(local.stages, ["prod-plan", "prod-breakglass"], [for s in local.stages : "drift-${s}"])
}

module "environments" {
  source = "../../../modules/github-environments"

  repository           = var.github_repository
  stages               = local.stages
  prod_reviewers       = var.prod_reviewers
  breakglass_reviewers = var.breakglass_reviewers
  # shared_units: plan the tunnel and Access changes of a pull request read-only against prod
  shared_units_pr_plan = true

  variables = { for env in local.ledger_envs : env => { AYASEE_LEDGER_URL = local.ledger_url } }
}

output "environments" {
  value = module.environments.environments
}
