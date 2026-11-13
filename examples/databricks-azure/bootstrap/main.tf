terraform {
  required_version = ">= 1.9.0"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.12"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

# Run this once, by a person, with `az login` and a GitHub token that can administer the
# repository (GITHUB_TOKEN env var). Keep its state apart from the stage units' state.
provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

provider "github" {
  owner = var.github_owner
}

locals {
  # Every environment the ayasee workflows use, one identity each (never shared). A test keeps
  # this list equal to the environments of the called workflows.
  stages = ["dev", "uat", "prod"]
  # pull requests plan the first stage in <first stage>-plan (dev-plan here)
  pr_plan_environment = "${local.stages[0]}-plan"
  environments = concat(
    [local.pr_plan_environment],
    local.stages,
    ["prod-plan", "prod-breakglass"],
    [for s in local.stages : "drift-${s}"],
  )

  # plan_encryption.key_provider of .ayasee.yaml (the default, azure_vault: the key is a Key Vault
  # key, reached with the environment's identity, so no plan-key secret)
  key_provider = "azure_vault"

  # Environments that plan or apply prod need the plan-encryption key.
  key_environments = ["prod-plan", "prod", "prod-breakglass"]

  # R30: configuration is variables, not secrets. Empty optional values are not written.
  variables = {
    for env in local.environments : env => merge(
      {
        AZURE_CLIENT_ID       = module.identity[env].client_id
        AZURE_TENANT_ID       = var.tenant_id
        AZURE_SUBSCRIPTION_ID = var.subscription_id
      },
      var.ledger_url == "" || env == local.pr_plan_environment ? {} : { AYASEE_LEDGER_URL = var.ledger_url },
      var.key_vault_key_id == "" || !contains(local.key_environments, env) ? {} : { AYASEE_KEY_VAULT_KEY_ID = var.key_vault_key_id },
    )
  }
}

module "identity" {
  source   = "../../../modules/azure-stage-identity"
  for_each = toset(local.environments)

  repository          = "${var.github_owner}/${var.github_repository}"
  environment         = each.key
  resource_group_name = var.resource_group_name
  location            = var.location
}

module "environments" {
  source = "../../../modules/github-environments"

  repository           = var.github_repository
  stages               = local.stages
  prod_reviewers       = var.prod_reviewers
  breakglass_reviewers = var.breakglass_reviewers
  variables            = local.variables

  owner        = var.github_owner
  key_provider = local.key_provider
  # identity: oidc, so no environment secret; the callers pass only teams-webhook
  repository_secret_scopes = {
    TEAMS_WEBHOOK = "the Teams incoming-webhook URL of the channel ayasee notifies (notify.teams; optional)"
  }
}

# Fails the plan if the module and the identity list above ever disagree.
check "environments_match" {
  assert {
    condition     = toset(module.environments.environments) == toset(local.environments)
    error_message = "github-environments creates a different set of environments than the identities cover."
  }
}
