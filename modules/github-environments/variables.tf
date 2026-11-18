variable "repository" {
  description = "Name of the GitHub repository (not owner/name)."
  type        = string
}

variable "stages" {
  description = "Stage names. Each gets an environment and a drift-<stage> environment. Must include prod."
  type        = list(string)
  default     = ["dev", "uat", "prod"]

  validation {
    condition     = contains(var.stages, "prod")
    error_message = "stages must include \"prod\"."
  }

  # the first stage names the pull-request plan environment, <first stage>-plan, which accepts
  # every branch: it must never be prod-plan, nor collide with another environment (issue #11)
  validation {
    condition     = try(var.stages[0], "prod") != "prod"
    error_message = "the first stage must be a pre-prod stage: pull requests plan it in <first stage>-plan."
  }

  validation {
    condition = alltrue([
      for s in var.stages : s == "prod" || (
        can(regex("^[a-z0-9][a-z0-9_-]*$", s)) &&
        !endswith(s, "-plan") && !startswith(s, "drift-") &&
        !contains(["prod-breakglass", "prod-pr"], s)
      )
    ])
    error_message = "stage names use [a-z0-9][a-z0-9_-]* and must not end in -plan, start with drift- or be prod-breakglass or prod-pr (reserved environment names)."
  }
}

variable "prod_reviewers" {
  description = "Required reviewers of the prod environment (GitHub team and user IDs). Must not be empty with gate_mechanism = \"environment\"; must be left unset (null) with \"workflow\", whose reviewers are reviewers.prod of .ayasee.yaml."
  type = object({
    teams = list(number)
    users = list(number)
  })
  default = null

  validation {
    condition     = var.gate_mechanism == "workflow" || try(length(var.prod_reviewers.teams) + length(var.prod_reviewers.users), 0) > 0
    error_message = "prod_reviewers must contain at least one team or user (DORA RTS 2024/1774 Art. 17(1)(b))."
  }

  validation {
    condition     = var.gate_mechanism != "workflow" || var.prod_reviewers == null
    error_message = "gate_mechanism = \"workflow\" sets no reviewers on prod: leave prod_reviewers unset and list the reviewers in reviewers.prod of .ayasee.yaml (D15)."
  }
}

variable "breakglass_reviewers" {
  description = "Required reviewers of prod-breakglass. Must not be empty and must differ from prod_reviewers. Left unset (null) with gate_mechanism = \"workflow\", whose break-glass approvers are reviewers.breakglass of .ayasee.yaml (checked by iac-approve)."
  type = object({
    teams = list(number)
    users = list(number)
  })
  default = null

  validation {
    condition     = var.gate_mechanism == "workflow" || try(length(var.breakglass_reviewers.teams) + length(var.breakglass_reviewers.users), 0) > 0
    error_message = "breakglass_reviewers must contain at least one team or user."
  }

  validation {
    condition     = var.gate_mechanism != "workflow" || var.breakglass_reviewers == null
    error_message = "gate_mechanism = \"workflow\" sets no reviewers on prod-breakglass: leave breakglass_reviewers unset and list the break-glass approvers in reviewers.breakglass of .ayasee.yaml (D15)."
  }

  validation {
    condition = var.solo_mode || var.gate_mechanism == "workflow" || !try(
      toset(var.breakglass_reviewers.teams) == toset(var.prod_reviewers.teams) &&
      toset(var.breakglass_reviewers.users) == toset(var.prod_reviewers.users),
      false
    )
    error_message = "breakglass_reviewers must differ from prod_reviewers (unless solo_mode)."
  }
}

variable "variables" {
  description = "Environment variables per environment: map of environment name to map of variable name to value (AZURE_CLIENT_ID etc.). No secrets."
  type        = map(map(string))
  default     = {}
}

variable "gate_mechanism" {
  description = "gate_mechanism of .ayasee.yaml (keep the two equal). \"environment\" (the default): prod and prod-breakglass carry required reviewers, which GitHub enforces. \"workflow\" (D15, a DEVIATION): no environment carries reviewers; ayasee's iac-approve workflow checks reviewers.prod (and, for break-glass, reviewers.breakglass) of .ayasee.yaml instead. The environments keep their main-only deployment branch policy where the plan has one; a private repository on GitHub Free (private_repository_plan = \"free\") has neither branch policies nor environment secrets and variables, so its environments are created bare and its secrets are repository secrets."
  type        = string
  default     = "environment"

  validation {
    condition     = contains(["environment", "workflow"], var.gate_mechanism)
    error_message = "gate_mechanism is environment or workflow."
  }
}

variable "shared_units_pr_plan" {
  description = "Create prod-pr-plan: the environment of the job that plans the shared prod units (shared_units in .ayasee.yaml) of a pull request read-only. With gate_mechanism = \"environment\" it is the plan-shared job of iac-pr: the environment accepts pull-request branches and carries prod_reviewers, so a prod reviewer approves before PR code runs with the read-only prod identity. With \"workflow\" (D15) it is iac-pr-shared, which a prod reviewer starts on main: the environment carries no reviewer and deploys from main only, and it is refused on GitHub Free (private_repository_plan = \"free\"), which can neither restrict it to main nor give it secrets of its own."
  type        = bool
  default     = false
}

variable "solo_mode" {
  description = "NON-COMPLIANT single-maintainer setup, to match solo_mode: true in .ayasee.yaml (DORA RTS 2024/1774 Art. 17(1)(b)). Lets prod_reviewers and breakglass_reviewers be the same person and turns prevent_self_review off on prod, prod-breakglass and prod-pr-plan, so that person can approve their own change. Reviewers are still required and admins still cannot bypass."
  type        = bool
  default     = false
}

variable "key_provider" {
  description = "plan_encryption.key_provider of .ayasee.yaml (azure_vault, pbkdf2, aws_kms, gcp_kms or openbao). It selects the plan-key secrets of prod-plan, prod and prod-breakglass that secret_commands lists; generate_plan_passphrase needs pbkdf2."
  type        = string
  default     = "azure_vault"

  validation {
    condition     = contains(["azure_vault", "pbkdf2", "aws_kms", "gcp_kms", "openbao"], var.key_provider)
    error_message = "key_provider must be azure_vault, pbkdf2, aws_kms, gcp_kms or openbao."
  }
}

variable "generate_plan_passphrase" {
  description = "Opt-in, key_provider pbkdf2 only: generate AYASEE_PLAN_PASSPHRASE (random_password, 64 characters of [A-Za-z0-9]) and set it as an environment secret of prod-plan, prod and prod-breakglass. The passphrase is then stored in plain text in the state of the root that calls this module: whoever reads that state can decrypt the approved prod plans. Turning it on replaces a passphrase set by hand; turning it off deletes the secret."
  type        = bool
  default     = false

  validation {
    condition     = !var.generate_plan_passphrase || var.key_provider == "pbkdf2"
    error_message = "generate_plan_passphrase needs key_provider = \"pbkdf2\"."
  }
}

variable "secret_scopes" {
  description = "Environment secrets this module cannot create (provider tokens, state keys), for the secret_commands output: map of environment name to map of secret name to the scope its value needs. Never values. The plan-key secrets follow key_provider and must not be listed here."
  type        = map(map(string))
  default     = {}

  validation {
    condition = alltrue(flatten([
      for env, secrets in var.secret_scopes : [
        for name in keys(secrets) : !contains(["AYASEE_PLAN_PASSPHRASE", "AYASEE_KMS_ACCESS_KEY_ID", "AYASEE_KMS_SECRET_ACCESS_KEY", "AYASEE_GCP_KMS_CREDENTIALS", "AYASEE_OPENBAO_TOKEN"], name)
      ]
    ]))
    error_message = "secret_scopes must not list the plan-key secrets; they follow key_provider."
  }
}

variable "repository_secret_scopes" {
  description = "Repository secrets to create by hand (AYASEE_LEDGER_TOKEN, TEAMS_WEBHOOK), for the secret_commands output: map of secret name to the scope its value needs. Never values."
  type        = map(string)
  default     = {}
}

variable "owner" {
  description = "Owner of the repository. When set, secret_commands carry --repo <owner>/<repository>; when empty, gh uses the repository of the current directory."
  type        = string
  default     = ""
}

variable "environment_prefix" {
  description = "environment_prefix of .ayasee.yaml (keep the two equal): put in front of the name of every GitHub environment this module creates, so that ayasee's environments never share a name with an application's own test or prod. With \"iac-\": iac-dev, iac-dev-plan, iac-prod-plan, iac-prod, iac-prod-breakglass, iac-drift-dev, ... The keys of variables and secret_scopes stay the unprefixed names (dev, prod-plan, drift-prod, ...); every output that names an environment gives the prefixed name. Empty (the default) keeps the names ayasee used before the setting existed."
  type        = string
  default     = ""

  validation {
    condition     = var.environment_prefix == "" || can(regex("^[a-z0-9][a-z0-9_-]{0,31}$", var.environment_prefix))
    error_message = "environment_prefix is empty or at most 32 characters of [a-z0-9_-] starting with a letter or digit (for example \"iac-\")."
  }
}

variable "adopt_existing_environments" {
  description = "Allow this module to take over a GitHub environment that already exists in the repository and was not created by it (no AYASEE_MANAGED variable). Default false: the plan fails naming the environment, because applying would overwrite its reviewers and deployment branch policy and share its secrets with ayasee's jobs. Set an environment_prefix instead, unless you mean to give an environment to ayasee. Upgrading a bootstrap created before this variable existed needs it once, to write the marker."
  type        = bool
  default     = false
}

variable "private_repository_plan" {
  description = "The GitHub plan of the repository's owner, for a private repository: free, pro, team or enterprise (GitHub Enterprise Cloud or Server). Ignored for a public or internal repository. A private repository has required reviewers on environments on GitHub Enterprise only, and deployment branch policies on Pro, Team or Enterprise: on any other plan, or when it is not stated (the default), the plan fails instead of creating environments without them (issue #39). `ayasee doctor` checks the plan against GitHub."
  type        = string
  default     = ""

  validation {
    condition     = contains(["", "free", "pro", "team", "enterprise"], var.private_repository_plan)
    error_message = "private_repository_plan is empty, free, pro, team or enterprise."
  }
}
