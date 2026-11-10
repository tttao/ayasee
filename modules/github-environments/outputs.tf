output "environments" {
  description = "Names of the environments created."
  value       = sort(keys(github_repository_environment.this))
}
