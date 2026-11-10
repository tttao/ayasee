output "client_ids" {
  description = "Client ID of each environment's identity (also written to the environment as AZURE_CLIENT_ID)."
  value       = { for env, m in module.identity : env => m.client_id }
}

output "principal_ids" {
  description = "Principal ID of each identity, for the role assignments of README step 2."
  value       = { for env, m in module.identity : env => m.principal_id }
}

output "environments" {
  description = "GitHub environments created."
  value       = module.environments.environments
}
