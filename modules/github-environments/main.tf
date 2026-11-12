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
  # dev-plan and prod-pr-plan are the only ones that are not restricted to main (PR plans).
  # prod-pr-plan (opt-in) plans the shared prod units of a PR read-only; it carries the prod
  # reviewers because the PR's code runs with a prod identity.
  pr_plan = merge(
    { "dev-plan" = null },
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

  main_only = { for k, v in local.environments : k => v if !contains(keys(local.pr_plan), k) }

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
