mock_provider "azurerm" {
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-identities/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mock"
    }
  }
}

variables {
  repository          = "acme/platform"
  environment         = "prod"
  resource_group_name = "rg-identities"
  location            = "westeurope"
}

run "subject_format" {
  command = plan

  assert {
    condition     = azurerm_federated_identity_credential.this.subject == "repo:acme/platform:environment:prod"
    error_message = "subject must be repo:<owner>/<repo>:environment:<environment>"
  }
  assert {
    condition     = output.subject == "repo:acme/platform:environment:prod"
    error_message = "subject output mismatch"
  }
  assert {
    condition     = azurerm_federated_identity_credential.this.issuer == "https://token.actions.githubusercontent.com"
    error_message = "default issuer must be the GitHub.com issuer"
  }
  assert {
    condition     = azurerm_federated_identity_credential.this.audience == tolist(["api://AzureADTokenExchange"])
    error_message = "audience must be api://AzureADTokenExchange"
  }
  assert {
    condition     = azurerm_user_assigned_identity.this.name == "ayasee-platform-prod"
    error_message = "identity name must be ayasee-<repo>-<environment>"
  }
}

run "breakglass_has_own_identity_and_subject" {
  command = plan
  variables {
    environment = "prod-breakglass"
  }

  assert {
    condition     = azurerm_user_assigned_identity.this.name == "ayasee-platform-prod-breakglass"
    error_message = "breakglass identity name"
  }
  assert {
    condition     = output.subject == "repo:acme/platform:environment:prod-breakglass"
    error_message = "breakglass subject"
  }
}

run "subject_override_is_honoured" {
  command = plan
  variables {
    subject = "repository_owner_id:1:repository_id:2:environment:prod"
  }

  assert {
    condition     = azurerm_federated_identity_credential.this.subject == "repository_owner_id:1:repository_id:2:environment:prod"
    error_message = "override must replace the default subject"
  }
  assert {
    condition     = output.subject == "repository_owner_id:1:repository_id:2:environment:prod"
    error_message = "output must report the override"
  }
}

run "custom_issuer" {
  command = plan
  variables {
    issuer = "https://ghes.example.com/_services/token"
  }

  assert {
    condition     = azurerm_federated_identity_credential.this.issuer == "https://ghes.example.com/_services/token"
    error_message = "issuer override"
  }
}

run "bad_repository_fails" {
  command = plan
  variables {
    repository = "no-owner"
  }
  expect_failures = [var.repository]
}

run "dotted_repository_name_is_sanitised_in_names_only" {
  command = plan
  variables {
    repository = "acme/my.repo"
  }

  assert {
    condition     = azurerm_user_assigned_identity.this.name == "ayasee-my-repo-prod"
    error_message = "a . in the repository name must become - in the identity name"
  }
  assert {
    condition     = azurerm_federated_identity_credential.this.name == "ayasee-my-repo-prod"
    error_message = "a . in the repository name must become - in the credential name"
  }
  assert {
    condition     = output.subject == "repo:acme/my.repo:environment:prod"
    error_message = "the subject keeps the real repository name"
  }
}

run "immutable_subject_override" {
  command = plan
  variables {
    subject = "repo:acme@123/platform@456:environment:prod"
  }

  assert {
    condition     = azurerm_federated_identity_credential.this.subject == "repo:acme@123/platform@456:environment:prod"
    error_message = "the immutable sub override must be used verbatim"
  }
}
