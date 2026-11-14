terraform {
  required_version = ">= 1.9.0"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.12" # the value argument of github_actions_environment_secret
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

locals {
  # The environment iac-pr plans a pull request in: <first stage>-plan (dev-plan with the default
  # stages, test-plan with [test, prod]), the name `ayasee config pr-plan-environment` prints.
  pr_plan_environment = "${var.stages[0]}-plan"

  # Environments the workflows use, and their reviewers (null = none).
  # <first stage>-plan and prod-pr-plan are the only ones that are not restricted to main (PR
  # plans). prod-pr-plan (opt-in) plans the shared prod units of a PR read-only; it carries the
  # prod reviewers because the PR's code runs with a prod identity.
  pr_plan = merge(
    { (local.pr_plan_environment) = null },
    var.shared_units_pr_plan ? { "prod-pr-plan" = var.prod_reviewers } : {},
  )

  environments = merge(
    { for s in var.stages : s => null },
    { for s in var.stages : "drift-${s}" => null },
    {
      "prod-plan"       = null
      "prod"            = var.prod_reviewers
      "prod-breakglass" = var.breakglass_reviewers
    },
    local.pr_plan,
  )

  self_reviewed = ["prod", "prod-breakglass", "prod-pr-plan"]

  main_only = { for k, v in local.environments : k => v if !contains(keys(local.pr_plan), k) }

  # The environments that plan or apply prod hold the plan key: the pbkdf2 passphrase, the
  # aws_kms key pair, the gcp_kms key file or the openbao token (plan_encryption.key_provider;
  # ayasee doctor checks the same three).
  key_environments = ["prod-plan", "prod", "prod-breakglass"]

  # Plan-key secrets this module knows but does not create, with the value each needs.
  key_secret_scopes = {
    azure_vault = {}
    pbkdf2 = var.generate_plan_passphrase ? {} : {
      AYASEE_PLAN_PASSPHRASE = "one passphrase, the same on prod-plan, prod and prod-breakglass: 32 or more characters of [A-Za-z0-9+/=_.-] (openssl rand -base64 48)"
    }
    aws_kms = {
      AYASEE_KMS_ACCESS_KEY_ID     = "access key id of an IAM user whose only permissions are kms:GenerateDataKey and kms:Decrypt on the AYASEE_KMS_KEY_ID key"
      AYASEE_KMS_SECRET_ACCESS_KEY = "the secret access key of that IAM user"
    }
    gcp_kms = {
      AYASEE_GCP_KMS_CREDENTIALS = "JSON key file of a service account whose only role is roles/cloudkms.cryptoKeyEncrypterDecrypter on the AYASEE_GCP_KMS_KEY_NAME key (gh secret set ... < key.json)"
    }
    openbao = {
      AYASEE_OPENBAO_TOKEN = "OpenBao token whose only policy allows update on <transit mount>/datakey/plaintext/<key> and <transit mount>/decrypt/<key> of the AYASEE_OPENBAO_KEY_URL key, with a TTL longer than a plan waits for approval"
    }
  }

  # Every secret still to create by hand, keyed "<environment>/<name>" ("/<name>" for a
  # repository secret) so that the for expression below lists them in a stable order.
  pending_secrets = merge(
    { for name, scope in var.repository_secret_scopes : "/${name}" => { environment = "", name = name, scope = scope } },
    merge([
      for env in local.key_environments : {
        for name, scope in local.key_secret_scopes[var.key_provider] :
        "${env}/${name}" => { environment = env, name = name, scope = scope }
      }
    ]...),
    merge([
      for env, secrets in var.secret_scopes : {
        for name, scope in secrets : "${env}/${name}" => { environment = env, name = name, scope = scope }
      }
    ]...),
  )

  repo_flag = var.owner == "" ? "" : " --repo ${var.owner}/${var.repository}"

  # `gh secret set` without --body prompts for the value (or reads it from stdin): no value ever
  # appears on a command line, in this output or in a shell history.
  secret_commands = join("", [
    for k, s in local.pending_secrets : format(
      "gh secret set %s%s%s   # %s\n",
      s.name, s.environment == "" ? "" : " --env ${s.environment}", local.repo_flag, s.scope,
    )
  ])

  variables = merge([
    for env, vars in var.variables : {
      for name, value in vars : "${env}/${name}" => {
        environment = env
        name        = name
        value       = value
      }
    }
  ]...)
}

resource "github_repository_environment" "this" {
  for_each = local.environments

  repository  = var.repository
  environment = each.key
  # solo_mode (NON-COMPLIANT): the one maintainer approves their own prod change
  prevent_self_review = !(var.solo_mode && contains(local.self_reviewed, each.key))
  can_admins_bypass   = false

  dynamic "reviewers" {
    for_each = each.value == null ? [] : [each.value]
    content {
      teams = reviewers.value.teams
      users = reviewers.value.users
    }
  }

  dynamic "deployment_branch_policy" {
    for_each = contains(keys(local.pr_plan), each.key) ? [] : [1]
    content {
      protected_branches     = false
      custom_branch_policies = true
    }
  }
}

resource "github_repository_environment_deployment_policy" "main" {
  for_each = local.main_only

  repository     = var.repository
  environment    = github_repository_environment.this[each.key].environment
  branch_pattern = "main"
}

resource "github_actions_environment_variable" "this" {
  for_each = local.variables

  repository    = var.repository
  environment   = github_repository_environment.this[each.value.environment].environment
  variable_name = each.value.name
  value         = each.value.value
}

# Opt-in (generate_plan_passphrase, key_provider pbkdf2 only): the plan passphrase, generated here
# and set on the three environments that plan or apply prod. github 6.x has no write-only secret
# argument, so the value is held in plain text in the state of the root that calls this module:
# protect that state as you would the passphrase (README).
resource "random_password" "plan_passphrase" {
  count = var.generate_plan_passphrase ? 1 : 0

  length  = 64
  special = false # [A-Za-z0-9] only: inside the [A-Za-z0-9+/=_.-] the plan step accepts
}

resource "github_actions_environment_secret" "plan_passphrase" {
  for_each = var.generate_plan_passphrase ? toset(local.key_environments) : toset([])

  repository  = var.repository
  environment = github_repository_environment.this[each.key].environment
  secret_name = "AYASEE_PLAN_PASSPHRASE"
  value       = random_password.plan_passphrase[0].result
}
