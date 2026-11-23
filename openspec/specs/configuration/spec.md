# configuration Specification

## Purpose

`.ayasee.yaml` at the repository root is the single adopter-facing configuration of ayasee. It is
parsed strictly and fails closed, exposes derived values to the reusable workflows through
`ayasee config <subcommand>`, and labels every opt-in that weakens a compliant default. Code:
`src/ayasee/config.py`. Reference: `docs/reference/ayasee-yaml.md`.

## Requirements

### Requirement: Optional file with defaults
The system SHALL treat a missing or empty `.ayasee.yaml` as a configuration in which every key takes its default (`stages: [dev, uat, prod]`, `engine: opentofu`, `terragrunt: true`, `iac_root: .`, `units_root: stages`, `gate: always`, `gate_mechanism: environment`, `identity: oidc`, `plan_encryption.key_provider: azure_vault`, `solo_mode: false`, `allow_cascade: false`, `environment_prefix: ""`).

#### Scenario: No configuration file
- **WHEN** `ayasee config show` runs in a repository without `.ayasee.yaml`
- **THEN** it prints the default configuration as JSON and exits 0

### Requirement: Strict, fail-closed validation
The system SHALL reject an unknown key at any level, a value of the wrong type or an invalid literal with a configuration error and exit code 1.

#### Scenario: Unknown key
- **WHEN** `.ayasee.yaml` contains a key that is not part of the schema
- **THEN** every `ayasee config` subcommand exits 1 with a message naming the problem

#### Scenario: Placeholder reviewer
- **WHEN** `reviewers.prod` still holds the placeholder `REPLACE_WITH_GITHUB_LOGIN`
- **THEN** the configuration does not load, so no reusable workflow run and no `ayasee doctor` passes

### Requirement: Stage list rules
The system SHALL require `stages` to be a non-empty list of unique names that contains `prod`; for the staged run it SHALL accept only one to four pre-prod stages followed by `prod` as the last stage, with pre-prod names matching `[a-z0-9][a-z0-9_-]*` and not being `prod`, `prod-pr`, `prod-breakglass`, starting with `drift-` or ending in `-plan`.

#### Scenario: Reserved stage name
- **WHEN** `stages` is `[dev, drift-x, prod]`
- **THEN** `ayasee config staged` exits 1 with a configuration error

#### Scenario: Too many stages
- **WHEN** `stages` lists six or more stages
- **THEN** `ayasee config staged` refuses the list

### Requirement: Path roots stay inside the repository
The system SHALL require `iac_root` to be relative and inside the repository, and `units_root` (relative to the repository root) to be inside `iac_root`; when a `units_root` outside `iac_root` looks written relative to it, the error SHALL name the path to write instead.

#### Scenario: iac_root set alone
- **WHEN** `.ayasee.yaml` sets `iac_root: infra/iac` and leaves `units_root` at its default `stages`
- **THEN** loading fails and the message suggests `units_root: infra/iac/stages`

### Requirement: Settings not supported yet are refused
The system SHALL accept `gate: risky`, `engine: terraform`, `terragrunt: false`, a non-empty `extra_rules` and `notify.teams: false` in the parser, but `ayasee config check-supported` SHALL exit 1 with one `<key>: not supported yet` line per such setting (for `gate`, "gate: risky is not supported yet"), and the first job of every reusable workflow SHALL run it before any cloud login.

#### Scenario: gate risky
- **WHEN** a repository sets `gate: risky` and pushes to `main`
- **THEN** the `resolve` job of `iac-staged` fails with "gate: risky is not supported yet" and nothing is planned

### Requirement: Compliance labels on every load
The system SHALL print `NON-COMPLIANT: solo_mode ...` when `solo_mode` is true, `DEVIATION: identity: secrets ...` when `identity` is `secrets`, and `DEVIATION: gate enforced by ayasee, not by GitHub environment protection ...` when `gate_mechanism` is `workflow`, to stderr on every load; `ayasee config labels` SHALL print one line per such opt-in so the workflows can raise a warning and a run-summary line.

#### Scenario: Solo mode run
- **WHEN** any reusable workflow runs with `solo_mode: true`
- **THEN** its first job emits a warning and a run-summary line starting with `NON-COMPLIANT: solo_mode`

### Requirement: Gate-mechanism dependent keys
The system SHALL require at least one login in `reviewers.prod` when `gate_mechanism` is `workflow`, SHALL refuse `reviewers.prod` and `reviewers.breakglass` when `gate_mechanism` is `environment`, SHALL treat logins as unique case-insensitively, and SHALL refuse a `reviewers.breakglass` list equal to `reviewers.prod` unless `solo_mode` is true.

#### Scenario: Reviewers with the environment gate
- **WHEN** `gate_mechanism` is `environment` and `reviewers.prod` is set
- **THEN** the configuration is refused

### Requirement: Ledger URL endpoint only from the variable
The system SHALL refuse an `s3://` `ledger.url` that carries an `endpoint=` parameter (even an empty one) in `.ayasee.yaml`, because the file is pull-request-controlled code; such a URL SHALL only come from the `AYASEE_LEDGER_URL` environment variable.

#### Scenario: Endpoint in the file
- **WHEN** `ledger.url` is `s3://bucket?region=auto&endpoint=https://example.com`
- **THEN** every `ayasee config` command exits 1

### Requirement: Derived values for the workflows
The system SHALL expose derived values: `binding_mode` (read-only: `encrypted_plan` for `opentofu`, `replan` for `terraform`), the stage-to-slot mapping (`config staged`), the pull-request plan environment `<environment_prefix><first stage>-plan` (`config pr-plan-environment`), and every GitHub environment name with the prefix applied (`config environments`).

#### Scenario: Two-stage pull-request environment
- **WHEN** `stages` is `[test, prod]` and `environment_prefix` is `iac-`
- **THEN** `ayasee config pr-plan-environment` prints `iac-test-plan`, and with `--logical` prints `test-plan`

### Requirement: Environment prefix applies only to GitHub-resolved names
The system SHALL prefix every name GitHub resolves (job environments, environment secrets and variables, approvals lookups, deployment mirrors, federated subjects, doctor checks) with `environment_prefix`, and SHALL NOT prefix names only ayasee reads (stage names, unit paths, ledger records, guard predecessor chain, drift issue titles and `env:` labels, notification `env`). The prefix SHALL be at most 32 characters of `[a-z0-9_-]` starting with a letter or digit.

#### Scenario: Prefixed estate
- **WHEN** `environment_prefix` is `iac-`
- **THEN** the prod apply job runs in environment `iac-prod` while its ledger record is still keyed `prod`
