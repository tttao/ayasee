output "client_id" {
  description = "Client ID, for the AZURE_CLIENT_ID environment variable."
  value       = azurerm_user_assigned_identity.this.client_id
}

output "principal_id" {
  description = "Principal (object) ID, for role assignments."
  value       = azurerm_user_assigned_identity.this.principal_id
}

output "subject" {
  description = "Subject of the federated credential."
  value       = azurerm_federated_identity_credential.this.subject
}
