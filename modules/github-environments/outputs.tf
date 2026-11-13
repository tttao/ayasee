output "environments" {
  description = "Names of the environments created."
  value       = sort(keys(github_repository_environment.this))
}

output "pr_plan_environment" {
  description = "The environment iac-pr plans a pull request in: <first stage>-plan (dev-plan with the default stages)."
  value       = local.pr_plan_environment
}
