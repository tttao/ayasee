mock_provider "github" {}
mock_provider "random" {}

variables {
  repository = "demo"
  stages     = ["test", "prod"]
  prod_reviewers = {
    teams = [101]
    users = []
  }
  breakglass_reviewers = {
    teams = [202]
    users = []
  }
}

run "no_secret_is_created_by_default" {
  command = plan

  assert {
    condition     = length(random_password.plan_passphrase) == 0 && length(github_actions_environment_secret.plan_passphrase) == 0
    error_message = "the passphrase is opt-in"
  }
  assert {
    condition     = output.secret_commands == ""
    error_message = "azure_vault with no declared secrets leaves nothing to set by hand"
  }
}

run "generated_passphrase_is_set_on_the_three_prod_key_environments_only" {
  command = apply
  variables {
    key_provider             = "pbkdf2"
    generate_plan_passphrase = true
  }

  assert {
    condition     = output.plan_passphrase_environments == tolist(["prod", "prod-breakglass", "prod-plan"])
    error_message = "AYASEE_PLAN_PASSPHRASE goes to prod-plan, prod and prod-breakglass, nowhere else"
  }
  assert {
    condition = alltrue([
      for s in github_actions_environment_secret.plan_passphrase :
      s.secret_name == "AYASEE_PLAN_PASSPHRASE" && s.value == random_password.plan_passphrase[0].result
    ])
    error_message = "the three environments must hold one and the same passphrase"
  }
  assert {
    condition     = random_password.plan_passphrase[0].length == 64 && random_password.plan_passphrase[0].special == false
    error_message = "64 characters of [A-Za-z0-9], inside the character set the plan step accepts"
  }
  assert {
    condition     = !strcontains(output.secret_commands, "AYASEE_PLAN_PASSPHRASE")
    error_message = "a generated passphrase is not listed as still to set"
  }
  assert {
    condition     = !strcontains(output.secret_commands, random_password.plan_passphrase[0].result) && !strcontains(jsonencode(output.pending_secrets), random_password.plan_passphrase[0].result)
    error_message = "the passphrase must never appear in an output"
  }
}

run "pbkdf2_without_generation_lists_the_passphrase_commands" {
  command = plan
  variables {
    key_provider = "pbkdf2"
  }

  assert {
    condition     = toset([for s in output.pending_secrets : s.environment if s.name == "AYASEE_PLAN_PASSPHRASE"]) == toset(["prod-plan", "prod", "prod-breakglass"])
    error_message = "the passphrase must be listed for the three prod key environments"
  }
  assert {
    condition     = strcontains(output.secret_commands, "gh secret set AYASEE_PLAN_PASSPHRASE --env prod-plan   # ")
    error_message = "the exact gh command must be printed"
  }
  assert {
    condition     = length(github_actions_environment_secret.plan_passphrase) == 0
    error_message = "nothing is generated without the opt-in"
  }
}

run "generation_needs_pbkdf2" {
  command = plan
  variables {
    key_provider             = "aws_kms"
    generate_plan_passphrase = true
  }
  expect_failures = [var.generate_plan_passphrase]
}

run "unknown_key_provider_fails" {
  command = plan
  variables {
    key_provider = "vault"
  }
  expect_failures = [var.key_provider]
}

run "gcp_kms_lists_the_key_file_on_the_three_environments" {
  command = plan
  variables {
    key_provider = "gcp_kms"
  }

  assert {
    condition = toset([
      for s in output.pending_secrets : s.environment if s.name == "AYASEE_GCP_KMS_CREDENTIALS"
    ]) == toset(["prod-plan", "prod", "prod-breakglass"])
    error_message = "the service-account key file on prod-plan, prod and prod-breakglass only"
  }
  assert {
    condition     = !strcontains(output.secret_commands, "AYASEE_PLAN_PASSPHRASE") && !strcontains(output.secret_commands, "AYASEE_KMS_")
    error_message = "gcp_kms needs no other key secret"
  }
}

run "openbao_lists_the_token_on_the_three_environments" {
  command = plan
  variables {
    key_provider = "openbao"
  }

  assert {
    condition = toset([
      for s in output.pending_secrets : s.environment if s.name == "AYASEE_OPENBAO_TOKEN"
    ]) == toset(["prod-plan", "prod", "prod-breakglass"])
    error_message = "the OpenBao token on prod-plan, prod and prod-breakglass only"
  }
  assert {
    condition     = !strcontains(output.secret_commands, "AYASEE_PLAN_PASSPHRASE") && !strcontains(output.secret_commands, "AYASEE_GCP_KMS_")
    error_message = "openbao needs no other key secret"
  }
}

run "secret_scopes_must_not_list_a_gcp_or_openbao_key_secret" {
  command = plan
  variables {
    secret_scopes = {
      prod = { AYASEE_OPENBAO_TOKEN = "x" }
    }
  }
  expect_failures = [var.secret_scopes]
}

run "aws_kms_lists_the_key_pair_on_the_three_environments" {
  command = plan
  variables {
    key_provider = "aws_kms"
  }

  assert {
    condition = length([
      for s in output.pending_secrets : s
      if contains(["AYASEE_KMS_ACCESS_KEY_ID", "AYASEE_KMS_SECRET_ACCESS_KEY"], s.name) &&
      contains(["prod-plan", "prod", "prod-breakglass"], s.environment)
    ]) == 6
    error_message = "both halves of the KMS key pair on prod-plan, prod and prod-breakglass"
  }
  assert {
    condition     = !strcontains(output.secret_commands, "AYASEE_PLAN_PASSPHRASE")
    error_message = "aws_kms needs no passphrase"
  }
}

run "declared_scopes_become_commands_with_repo_and_scope" {
  command = plan
  variables {
    owner = "my-org"
    secret_scopes = {
      test = { CLOUDFLARE_API_TOKEN = "Zone DNS Edit" }
    }
    repository_secret_scopes = { AYASEE_LEDGER_TOKEN = "Contents: read and write on this repository only" }
  }

  assert {
    condition     = strcontains(output.secret_commands, "gh secret set CLOUDFLARE_API_TOKEN --env test --repo my-org/demo   # Zone DNS Edit\n")
    error_message = "an environment secret command names the environment, the repository and the scope"
  }
  assert {
    condition     = startswith(output.secret_commands, "gh secret set AYASEE_LEDGER_TOKEN --repo my-org/demo   # Contents")
    error_message = "repository secrets come first and carry no --env"
  }
}

run "scopes_for_an_unknown_environment_fail" {
  command = plan
  variables {
    secret_scopes = {
      staging = { CLOUDFLARE_API_TOKEN = "Zone DNS Edit" }
    }
  }
  expect_failures = [output.pending_secrets, output.secret_commands]
}

run "plan_key_secrets_cannot_be_declared_by_hand" {
  command = plan
  variables {
    secret_scopes = {
      prod = { AYASEE_PLAN_PASSPHRASE = "x" }
    }
  }
  expect_failures = [var.secret_scopes]
}
