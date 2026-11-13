output "environments" {
  description = "Names of the environments created."
  value       = sort(keys(github_repository_environment.this))
}

output "pr_plan_environment" {
  description = "The environment iac-pr plans a pull request in: <first stage>-plan (dev-plan with the default stages)."
  value       = local.pr_plan_environment
}

output "plan_passphrase_environments" {
  description = "Environments on which this module set AYASEE_PLAN_PASSPHRASE (empty unless generate_plan_passphrase). Never the value."
  value       = sort(keys(github_actions_environment_secret.plan_passphrase))
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
