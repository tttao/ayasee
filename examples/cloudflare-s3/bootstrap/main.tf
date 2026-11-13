# The GitHub environments of the Cloudflare and S3 reference repository. There is no identity
# module: with identity: secrets, each environment holds provider tokens (README, step 3).
terraform {
  required_version = ">= 1.10.0"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.12"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
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

variable "generate_plan_passphrase" {
  description = "Opt-in: generate AYASEE_PLAN_PASSPHRASE and set it on prod-plan, prod and prod-breakglass. The passphrase is then in this root's state in plain text: keep that state private (README, step 3)."
  type        = bool
  default     = false
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

  # plan_encryption.key_provider of .ayasee.yaml
  key_provider = "pbkdf2"
  # shared_units: plan the tunnel and Access changes of a pull request read-only against prod, in
  # prod-pr-plan
  shared_units_pr_plan = true

  # The provider tokens each environment needs (README, step 3), for the secret_commands output:
  # every environment ayasee doctor checks with identity: secrets. Cloudflare tokens are scoped to
  # zones and accounts, not records: a pre-prod token can edit every DNS record of the zone, the
  # prod ones included.
  preprod  = [for s in local.stages : s if s != "prod"]
  cf_read  = "Cloudflare token: Zone DNS Read, Account Cloudflare Tunnel Read, Access: Apps and Policies Read"
  cf_edit  = "Cloudflare token: Zone DNS Edit, Account Cloudflare Tunnel Edit, Access: Apps and Policies Edit"
  s3_read  = "S3 key pair: read on the state bucket of"
  s3_write = "S3 key pair: read and write on the state bucket of"
  token_scopes = merge(
    { for s in local.preprod : s => { cf = "Cloudflare token: Zone DNS Edit", s3 = "${local.s3_write} ${s}" } },
    { for s in local.preprod : "drift-${s}" => { cf = "Cloudflare token: Zone DNS Read", s3 = "${local.s3_write} ${s} (the lock file)" } },
    {
      "${local.stages[0]}-plan" = { cf = "Cloudflare token: Zone DNS Read", s3 = "${local.s3_read} ${local.stages[0]}" }
      "prod-pr-plan"            = { cf = local.cf_read, s3 = "${local.s3_read} prod" }
      "prod-plan"               = { cf = local.cf_read, s3 = "${local.s3_write} prod (the lock file)" }
      "prod"                    = { cf = local.cf_edit, s3 = "${local.s3_write} prod" }
      "prod-breakglass"         = { cf = local.cf_edit, s3 = "${local.s3_write} prod" }
      "drift-prod"              = { cf = local.cf_read, s3 = "${local.s3_write} prod (the lock file)" }
    },
  )
}

module "environments" {
  source = "../../../modules/github-environments"

  repository           = var.github_repository
  owner                = var.github_owner
  stages               = local.stages
  prod_reviewers       = var.prod_reviewers
  breakglass_reviewers = var.breakglass_reviewers
  shared_units_pr_plan = local.shared_units_pr_plan

  variables = { for env in local.ledger_envs : env => { AYASEE_LEDGER_URL = local.ledger_url } }

  key_provider             = local.key_provider
  generate_plan_passphrase = var.generate_plan_passphrase
  secret_scopes = {
    for env, s in local.token_scopes : env => {
      CLOUDFLARE_API_TOKEN  = s.cf
      AWS_ACCESS_KEY_ID     = s.s3
      AWS_SECRET_ACCESS_KEY = s.s3
    } if env != "prod-pr-plan" || local.shared_units_pr_plan
  }
  repository_secret_scopes = {
    AYASEE_LEDGER_TOKEN = "fine-grained token, this repository only, Contents: read and write and nothing else; require pull requests on main with a ruleset (README, step 4)"
    TEAMS_WEBHOOK       = "the Teams incoming-webhook URL of the channel ayasee notifies (notify.teams)"
  }
}

output "environments" {
  value = module.environments.environments
}

output "secret_commands" {
  description = "The gh secret set commands still to run, each with the scope its value needs: tofu output -raw secret_commands."
  value       = module.environments.secret_commands
}
