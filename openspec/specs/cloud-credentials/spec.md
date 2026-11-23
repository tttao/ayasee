# cloud-credentials Specification

## Purpose

How stage jobs reach the cloud (D7): OIDC with one Entra managed identity per GitHub environment by
default, or the opt-in, labelled `identity: secrets` profile with environment-scoped provider tokens
and a declared provider environment. Code: `src/ayasee/providerenv.py`, the reusable workflows.
Reference: `docs/concepts/staged-run.md#identity-secrets`.

## Requirements

### Requirement: OIDC by default
With `identity: oidc` the workflows SHALL run `azure/login` with the environment's `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID` variables, set `ARM_USE_OIDC=true`, the `ARM_*` variables and `DATABRICKS_AUTH_TYPE=github-oidc-azure`, and store no cloud secret; provider-token steps SHALL receive empty strings.

#### Scenario: dev stage login
- **WHEN** the `dev` stage job runs with the default identity
- **THEN** it logs in with the `dev` environment's federated identity

### Requirement: identity secrets profile
With `identity: secrets` the workflows SHALL skip `azure/login`, leave `ARM_*` and `DATABRICKS_AUTH_TYPE` empty, and pass `CLOUDFLARE_API_TOKEN`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (declared optional `workflow_call` secrets, passed by name) to the plan and apply steps only.

#### Scenario: Guard step
- **WHEN** a guard step runs with `identity: secrets`
- **THEN** it does not receive `CLOUDFLARE_API_TOKEN`

### Requirement: Provider environment is declared and checked
With `identity: secrets`, the plan and apply scripts SHALL run `ayasee provider-env` on the `AYASEE_PROVIDER_ENV` secret (a JSON object of name to string) and SHALL refuse the run when it holds a key not declared in `provider_env` (counted, never printed), a non-string value, a repeated key or malformed JSON (error with line and column only).

#### Scenario: Undeclared key
- **WHEN** the secret holds `HCLOUD_ENDPOINT` and `provider_env` does not declare it
- **THEN** the plan step fails without printing the key

### Requirement: Reserved names refused
The system SHALL refuse reserved names both in `provider_env` (configuration load) and at run time: names that change what the job runs, where traffic goes or what it logs (`PATH`, `HOME`, loader and interpreter variables, bash variables, `GITHUB_*`, `RUNNER_*`, `ACTIONS_*`, `TF_*` other than `TF_VAR_*`, `TG_*`, `TERRAGRUNT_*`, `TERRAFORM_*`, `SG_*`, `AYASEE_*`, `GIT_*`, `SSH_*`, TLS and proxy variables, `AWS_*`, `CLOUDFLARE_*`, and the rest of `RESERVED`/`RESERVED_PREFIXES`). Names SHALL be `[A-Z][A-Z0-9_]*` or `TF_VAR_<variable>`, and a non-empty `provider_env` with `identity: oidc` SHALL be refused.

#### Scenario: Proxy variable declared
- **WHEN** `provider_env` lists `HTTPS_PROXY`
- **THEN** the configuration does not load

### Requirement: Values long enough to mask safely
Each provider-env value SHALL have at least 16 characters after trimming, SHALL be registered with `::add-mask::` before anything else is printed (a multi-line value by each line of 8 characters or more), and a value with more than one shorter line SHALL be refused.

#### Scenario: Short value
- **WHEN** a value is `true`
- **THEN** the run is refused, because masking it would blank job outputs such as `has-changes`

### Requirement: Values never leave the step
Provider-env variables SHALL be exported only in the plan or apply script's process and its engine, never through `$GITHUB_ENV`, a command line or a later step; the pairs SHALL pass through a mode-600 file under `$RUNNER_TEMP` deleted before export, and the raw JSON SHALL be unset in the script's shell.

#### Scenario: Next step
- **WHEN** the apply step finishes
- **THEN** the following step's environment holds none of the provider-env variables

### Requirement: Deviation is labelled
`identity: secrets` SHALL print `DEVIATION: identity: secrets ...` on every load and be raised as a warning and run-summary line in the first job of every reusable workflow; the cockpit SHALL tag the repository.

#### Scenario: Run summary
- **WHEN** any reusable workflow runs with `identity: secrets`
- **THEN** its summary contains the DEVIATION line
