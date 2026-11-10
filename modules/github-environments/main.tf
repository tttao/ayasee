terraform {
  required_version = ">= 1.9.0"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.6"
    }
  }
}

locals {
  # Environments the workflows use, and their reviewers (null = none).
  # dev-plan is the only one that is not restricted to main (PR plans).
  environments = merge(
    { for s in var.stages : s => null },
    { for s in var.stages : "drift-${s}" => null },
    {
      "dev-plan"        = null
      "prod-plan"       = null
      "prod"            = var.prod_reviewers
      "prod-breakglass" = var.breakglass_reviewers
    },
  )

  main_only = { for k, v in local.environments : k => v if k != "dev-plan" }

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

  repository          = var.repository
  environment         = each.key
  prevent_self_review = true
  can_admins_bypass   = false

  dynamic "reviewers" {
    for_each = each.value == null ? [] : [each.value]
    content {
      teams = reviewers.value.teams
      users = reviewers.value.users
    }
  }

  dynamic "deployment_branch_policy" {
    for_each = each.key == "dev-plan" ? [] : [1]
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
