output "environments" {
  description = "GitHub names of the environments created (environment_prefix included)."
  value       = sort([for e in github_repository_environment.this : e.environment])
}

output "environment_names" {
  description = "Map of the unprefixed environment name (dev, prod-plan, drift-prod, ...) to its GitHub name; the keys are what variables and secret_scopes use."
  value       = { for k, e in github_repository_environment.this : k => e.environment }
}

output "pr_plan_environment" {
  description = "The GitHub environment iac-pr plans a pull request in: <environment_prefix><first stage>-plan (dev-plan with the default stages and no prefix)."
  value       = "${var.environment_prefix}${local.pr_plan_environment}"
}

output "plan_passphrase_environments" {
  description = "GitHub environments on which this module set AYASEE_PLAN_PASSPHRASE (empty unless generate_plan_passphrase). Never the value."
  value       = sort([for s in github_actions_environment_secret.plan_passphrase : s.environment])
}

output "pending_secrets" {
  description = "Secrets still to create by hand: environment (\"\" for a repository secret), name, and the scope its value needs. Never values."
  value       = values(local.pending_secrets)

  precondition {
    condition     = alltrue([for env in keys(var.secret_scopes) : contains(keys(local.environments), env)])
    error_message = "secret_scopes names an environment this module does not create."
  }
}

output "secret_commands" {
  description = "One gh secret set command per pending secret, its scope as a comment. Print it with tofu output -raw and run each line; gh prompts for the value, so none is ever printed."
  value       = local.secret_commands

  precondition {
    condition     = alltrue([for env in keys(var.secret_scopes) : contains(keys(local.environments), env)])
    error_message = "secret_scopes names an environment this module does not create."
  }
}
