terraform {
  required_version = ">= 1.9.0"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.12" # the value argument of github_actions_environment_secret
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

locals {
  # gate_mechanism = "workflow" (D15): no environment carries reviewers; iac-approve checks them
  # (reviewers.prod, and reviewers.breakglass for the prod-breakglass apply of a break-glass request)
  workflow_gate = var.gate_mechanism == "workflow"

  # Everything below is keyed by the logical environment name (dev, prod-plan, drift-prod, ...), the
  # name the stages, the secrets and the variables of this module use. The GitHub environment is
  # var.environment_prefix plus that name (issue #31); local.github_name maps one to the other.
  github_name = { for k, _ in local.environments : k => "${var.environment_prefix}${k}" }

  # The environment iac-pr plans a pull request in: <first stage>-plan (dev-plan with the default
  # stages, test-plan with [test, prod]), the name `ayasee config pr-plan-environment` prints.
  pr_plan_environment = "${var.stages[0]}-plan"

  # "No reviewers" is this empty set, never null: Trivy (0.70.0 to at least 0.76.2) panics, at
  # random, on a for_each local whose elements include a null object, which the workflow gate's
  # unset prod_reviewers and breakglass_reviewers used to put in local.environments (#57).
  # Reviewers are required where the set is not empty (local.reviewed); the variable validations
  # keep the environment gate's prod and prod-breakglass sets non-empty, and a precondition below
  # checks it again.
  no_reviewers         = { teams = [], users = [] }
  prod_reviewers       = local.workflow_gate || var.prod_reviewers == null ? local.no_reviewers : var.prod_reviewers
  breakglass_reviewers = local.workflow_gate || var.breakglass_reviewers == null ? local.no_reviewers : var.breakglass_reviewers

  # Environments the workflows use, and their reviewers (local.no_reviewers = none).
  # <first stage>-plan and prod-pr-plan are the only ones that are not restricted to main (PR
  # plans). prod-pr-plan (opt-in) plans the shared prod units of a PR read-only; it carries the
  # prod reviewers because the PR's code runs with a prod identity. With the workflow gate (D15)
  # nothing can hold a job of the PR's own run, so iac-pr-shared plans from main instead, and
  # prod-pr-plan is a main-only environment without reviewers (workflow_pr_plan).
  pr_plan = merge(
    { (local.pr_plan_environment) = local.no_reviewers },
    var.shared_units_pr_plan && !local.workflow_gate ? { "prod-pr-plan" = local.prod_reviewers } : {},
  )
  workflow_pr_plan = var.shared_units_pr_plan && local.workflow_gate ? { "prod-pr-plan" = local.no_reviewers } : {}

  environments = merge(
    { for s in var.stages : s => local.no_reviewers },
    { for s in var.stages : "drift-${s}" => local.no_reviewers },
    {
      "prod-plan"       = local.no_reviewers
      "prod"            = local.prod_reviewers
      "prod-breakglass" = local.breakglass_reviewers
    },
    local.pr_plan,
    local.workflow_pr_plan,
  )

  # the environments whose deployments wait for required reviewers
  reviewed = { for k, v in local.environments : k => length(v.teams) + length(v.users) > 0 }

  self_reviewed = ["prod", "prod-breakglass", "prod-pr-plan"]

  main_only = { for k, v in local.environments : k => v if !contains(keys(local.pr_plan), k) }

  # The environments that plan or apply prod hold the plan key: the pbkdf2 passphrase, the
  # aws_kms key pair, the gcp_kms key file or the openbao token (plan_encryption.key_provider;
  # ayasee doctor checks the same three).
  key_environments = ["prod-plan", "prod", "prod-breakglass"]

  # Plan-key secrets this module knows but does not create, with the value each needs.
  key_secret_scopes = {
    azure_vault = {}
    pbkdf2 = var.generate_plan_passphrase ? {} : {
      AYASEE_PLAN_PASSPHRASE = "one passphrase, the same on prod-plan, prod and prod-breakglass: 32 or more characters of [A-Za-z0-9+/=_.-] (openssl rand -base64 48)"
    }
    aws_kms = {
      AYASEE_KMS_ACCESS_KEY_ID     = "access key id of an IAM user whose only permissions are kms:GenerateDataKey and kms:Decrypt on the AYASEE_KMS_KEY_ID key"
      AYASEE_KMS_SECRET_ACCESS_KEY = "the secret access key of that IAM user"
    }
    gcp_kms = {
      AYASEE_GCP_KMS_CREDENTIALS = "JSON key file of a service account whose only role is roles/cloudkms.cryptoKeyEncrypterDecrypter on the AYASEE_GCP_KMS_KEY_NAME key (gh secret set ... < key.json)"
    }
    openbao = {
      AYASEE_OPENBAO_TOKEN = "OpenBao token whose only policy allows update on <transit mount>/datakey/plaintext/<key> and <transit mount>/decrypt/<key> of the AYASEE_OPENBAO_KEY_URL key, with a TTL longer than a plan waits for approval"
    }
  }

  # Every environment secret the module cannot create: the plan key, then secret_scopes.
  environment_secrets = concat(
    flatten([
      for env in local.key_environments : [
        for name, scope in local.key_secret_scopes[var.key_provider] : { environment = env, name = name, scope = scope }
      ]
    ]),
    flatten([
      for env, secrets in var.secret_scopes : [
        for name, scope in secrets : { environment = env, name = name, scope = scope }
      ]
    ]),
  )

  # A bare environment (GitHub Free, private) has no secrets: one repository secret per name,
  # which every job reads, the pre-prod stages, drift and the pull-request plan as well as prod.
  # Its value therefore needs every scope listed for that name, not one of them (#47).
  bare_scopes = { for s in local.environment_secrets : s.name => s.scope... }

  # Every secret still to create by hand, keyed "<environment>/<name>" ("/<name>" for a
  # repository secret) so that the for expression below lists them in a stable order.
  pending_secrets = merge(
    { for name, scope in var.repository_secret_scopes : "/${name}" => { environment = "", name = name, scope = scope } },
    local.bare ? {
      for name, scopes in local.bare_scopes : "/${name}" => {
        environment = ""
        name        = name
        scope = length(distinct(scopes)) == 1 ? "${scopes[0]} (repository secret on GitHub Free: every stage and the pull-request plan read it)" : format(
          "repository secret on GitHub Free, read by every stage and by the pull-request plan, so ONE value needs ALL of: %s",
          join(" + ", distinct(scopes)),
        )
      }
      } : {
      for s in local.environment_secrets : "${s.environment}/${s.name}" => s
    },
  )

  # On GitHub Free a name in both lists would be one repository secret with two meanings.
  repository_secret_clash = local.bare ? sort(setintersection(keys(var.repository_secret_scopes), keys(local.bare_scopes))) : []

  repo_flag = var.owner == "" ? "" : " --repo ${var.owner}/${var.repository}"

  # `gh secret set` without --body prompts for the value (or reads it from stdin): no value ever
  # appears on a command line, in this output or in a shell history.
  secret_commands = join("", [
    for k, s in local.pending_secrets : format(
      "gh secret set %s%s%s   # %s\n",
      s.name, s.environment == "" ? "" : " --env ${var.environment_prefix}${s.environment}", local.repo_flag, s.scope,
    )
  ])

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

# Environments that already exist in the repository, whoever created them. Creating a GitHub
# environment is an upsert: without the check below, applying this module would silently rewrite the
# reviewers and the deployment branch policy of an application's own `test` or `prod`.
data "github_repository_environments" "existing" {
  repository = var.repository
}

locals {
  existing_environments = toset([for e in data.github_repository_environments.existing.environments : lower(e.name)])

  # the environments this module would create that already exist
  colliding = { for k, n in local.github_name : k => n if contains(local.existing_environments, lower(n)) }
}

# What tells an environment of ayasee from an application's: the marker variable this module sets
# on every environment it creates (below). Only the environments that already exist are read.
data "github_actions_environment_variables" "marker" {
  for_each = local.colliding

  name        = var.repository
  environment = each.value
}

locals {
  # existing environments without the marker: not created by this module
  foreign = toset([
    for k, n in local.colliding : n
    if !contains([for v in data.github_actions_environment_variables.marker[k].variables : v.name], local.marker_name)
  ])
  marker_name = "AYASEE_MANAGED"
}

# What the repository's GitHub plan lets an environment carry (issue #39). A public repository has
# every feature on every plan, an internal one exists on GitHub Enterprise only. A private one has
# deployment branch policies on GitHub Pro, Team or Enterprise, but required reviewers on
# environments on GitHub Enterprise only (Cloud or Server). The API does not show the plan to
# every administrator, so a private repository states it in private_repository_plan, which
# `ayasee doctor` checks against GitHub. Any other visibility is refused, never taken for public.
data "github_repository" "this" {
  name = var.repository
}

locals {
  visibility    = data.github_repository.this.visibility
  open_to_all   = contains(["public", "internal"], local.visibility)
  private       = local.visibility == "private"
  has_reviewers = local.open_to_all || (local.private && var.private_repository_plan == "enterprise")
  has_policies  = local.open_to_all || (local.private && contains(["pro", "team", "enterprise"], var.private_repository_plan))
  plan_label    = var.private_repository_plan == "" ? "not stated" : var.private_repository_plan

  # D15: a private repository on GitHub Free with the workflow gate. It has no deployment branch
  # policies, environment secrets or environment variables, so its environments are created bare
  # (no reviewers, no branch policy, no AYASEE_MANAGED variable) and every secret and variable is
  # a repository one, which any workflow of any branch can read. Stated, never inferred.
  bare = local.workflow_gate && local.private && var.private_repository_plan == "free"
}

resource "github_repository_environment" "this" {
  for_each = local.environments

  repository  = var.repository
  environment = local.github_name[each.key]

  lifecycle {
    # fail closed: an environment GitHub would create without its reviewers or its branch policy
    # turns the prod gate into a pass-through or opens the prod secrets to every branch
    precondition {
      condition     = (!local.reviewed[each.key] || local.has_reviewers) && (!contains(keys(local.main_only), each.key) || local.has_policies || local.bare)
      error_message = "The GitHub environment \"${local.github_name[each.key]}\" needs ${local.reviewed[each.key] ? "required reviewers" : "a main-only deployment branch policy"}, which this repository cannot hold: it is ${local.visibility}, and private_repository_plan is ${local.plan_label}. A private repository has required reviewers on environments on GitHub Enterprise only, and deployment branch policies on GitHub Pro, Team or Enterprise. Without them nothing waits for an approval before prod (prevent_self_review and the break-glass reviewers go with it) and any branch can read the prod secrets, so this module creates nothing. Make the repository public, move it to GitHub Enterprise and set private_repository_plan = \"enterprise\", or use gate_mechanism = \"workflow\" (D15, a DEVIATION: ayasee enforces the gate, not GitHub; docs/guides/adopt.md#what-you-need)."
    }
    precondition {
      condition     = !(local.bare && contains(keys(local.workflow_pr_plan), each.key))
      error_message = "shared_units_pr_plan with gate_mechanism = \"workflow\" on GitHub Free: \"${local.github_name[each.key]}\" would accept every branch (no deployment branch policies) and hold no secret of its own (no environment secrets), so a workflow of any branch could run with the repository's prod credentials. iac-pr-shared refuses such an environment; set shared_units_pr_plan = false (D15)."
    }
    precondition {
      condition     = !local.bare || (length(var.variables) == 0 && !var.generate_plan_passphrase)
      error_message = "A private repository on GitHub Free has no environment variables or secrets: set the variables (AYASEE_LEDGER_URL, ...) and the plan passphrase as repository variables and secrets instead, and leave variables empty and generate_plan_passphrase false (secret_commands lists them at repository level)."
    }
    # the environment gate: prod, prod-breakglass and prod-pr-plan always wait for reviewers (the
    # variable validations refuse an empty set; this holds even if "no reviewers" reached them)
    precondition {
      condition     = local.workflow_gate || !contains(local.self_reviewed, each.key) || local.reviewed[each.key]
      error_message = "With gate_mechanism = \"environment\", \"${local.github_name[each.key]}\" must carry required reviewers: set prod_reviewers and breakglass_reviewers."
    }
    # GitHub may store an environment without the reviewers it was given: check what it kept
    postcondition {
      condition     = !local.reviewed[each.key] || length(self.reviewers) > 0
      error_message = "GitHub did not keep the required reviewers of \"${local.github_name[each.key]}\": its job would run without an approval. Check the repository's plan (`ayasee doctor`); the staged and break-glass runs refuse such an environment."
    }
    precondition {
      condition     = var.adopt_existing_environments || !contains(local.foreign, local.github_name[each.key])
      error_message = "The GitHub environment \"${local.github_name[each.key]}\" already exists and was not created by ayasee (it has no ${local.marker_name} variable). Applying would overwrite its reviewers and deployment branch policy, and its secrets would be shared with ayasee's jobs. Set environment_prefix (for example \"iac-\", and the same in .ayasee.yaml) so ayasee gets environments of its own, or, to take this one over deliberately, set adopt_existing_environments = true. `ayasee doctor` lists the workflows that reference it."
    }
  }

  # solo_mode (NON-COMPLIANT): the one maintainer approves their own prod change. The workflow
  # gate sets no reviewers, so there is no review to prevent (iac-approve checks it, D15)
  prevent_self_review = local.workflow_gate ? false : !(var.solo_mode && contains(local.self_reviewed, each.key))
  can_admins_bypass   = false

  dynamic "reviewers" {
    for_each = local.reviewed[each.key] ? [each.value] : []
    content {
      teams = reviewers.value.teams
      users = reviewers.value.users
    }
  }

  dynamic "deployment_branch_policy" {
    for_each = contains(keys(local.pr_plan), each.key) || local.bare ? [] : [1]
    content {
      protected_branches     = false
      custom_branch_policies = true
    }
  }
}

resource "github_repository_environment_deployment_policy" "main" {
  for_each = local.bare ? {} : local.main_only

  repository     = var.repository
  environment    = github_repository_environment.this[each.key].environment
  branch_pattern = "main"
}

# Marks an environment as created by this module: the check above refuses an existing environment
# without it, and `ayasee doctor` reads it. A configuration variable, not a secret.
resource "github_actions_environment_variable" "marker" {
  for_each = local.bare ? {} : local.environments

  repository    = var.repository
  environment   = github_repository_environment.this[each.key].environment
  variable_name = local.marker_name
  value         = "true"
}

resource "github_actions_environment_variable" "this" {
  for_each = local.variables

  repository    = var.repository
  environment   = github_repository_environment.this[each.value.environment].environment
  variable_name = each.value.name
  value         = each.value.value
}

# Opt-in (generate_plan_passphrase, key_provider pbkdf2 only): the plan passphrase, generated here
# and set on the three environments that plan or apply prod. github 6.x has no write-only secret
# argument, so the value is held in plain text in the state of the root that calls this module:
# protect that state as you would the passphrase (README).
resource "random_password" "plan_passphrase" {
  count = var.generate_plan_passphrase ? 1 : 0

  length  = 64
  special = false # [A-Za-z0-9] only: inside the [A-Za-z0-9+/=_.-] the plan step accepts
}

resource "github_actions_environment_secret" "plan_passphrase" {
  for_each = var.generate_plan_passphrase ? toset(local.key_environments) : toset([])

  repository  = var.repository
  environment = github_repository_environment.this[each.key].environment
  secret_name = "AYASEE_PLAN_PASSPHRASE"
  value       = random_password.plan_passphrase[0].result
}
