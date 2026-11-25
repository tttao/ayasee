# plan-binding Specification

## Purpose

Plan binding makes the prod apply use the exact plan a reviewer approved, and stop when it cannot
prove that (decision D1). It covers the encrypted saved plan and its manifest, the binding modes of
the plan and apply actions, plan-encryption key providers, and the cascade rule. Code:
`src/ayasee/binding.py`, `src/ayasee/cascade.py`, `actions/plan/plan.sh`, `actions/apply/apply.sh`,
`actions/lib/ayasee-lib.sh`. Reference: `docs/concepts/plan-binding.md`.
## Requirements
### Requirement: Encrypted saved prod plan
In binding mode `encrypted_plan` the system SHALL plan each prod unit with `-out`, encrypted by OpenTofu's `plan {}` encryption with method `aes_gcm` and `enforced = true`, using the key from the configured key provider, and SHALL upload only `plans/<unit>.tfplan` and `manifest.json`.

#### Scenario: Plan JSON stays in the job
- **WHEN** `prod-plan` completes
- **THEN** the artifact `ayasee-prod-plan` holds encrypted plan files and the manifest only, never plan JSON

### Requirement: Manifest
`ayasee binding make` SHALL write `manifest.json` with schema `ayasee.manifest/v1` holding the commit SHA, environment, engine and engine version, and per unit `plan_sha256`, `lock_sha256` (provider lock file) and `values_sha256`.

#### Scenario: Manifest content
- **WHEN** a prod plan of two units is made
- **THEN** the manifest lists both units with the three hashes each

### Requirement: Ordered verification before apply
`ayasee binding verify` SHALL check, in this order and stopping at the first failure: the manifest SHA equals the commit being applied (`sha`), the engine version equals the planning one (`engine`), the set of units equals the manifest's (`unit_set`), each plan file's SHA-256 (`plan_hash`), each unit's provider lock hash (`lock_hash`). A mismatch SHALL exit 3 with "approved plan no longer matches: stop, re-plan and re-approve (nothing was applied)"; any other failure SHALL exit 1; nothing SHALL be applied in either case.

#### Scenario: Provider lock changed
- **WHEN** a unit's `.terraform.lock.hcl` resolves differently at apply time
- **THEN** verification fails with `lock_hash`, exit 3, and nothing is applied

### Requirement: Never re-plan silently
A stale or mismatched prod plan SHALL stop the run and ask for a re-plan and a new approval; the system SHALL NOT re-plan and apply in its place. OpenTofu's own stale-plan refusal (state serial moved) SHALL stand behind the manifest checks.

#### Scenario: State changed after approval
- **WHEN** the state serial changed between plan and apply
- **THEN** the engine refuses the saved plan and the run stops

### Requirement: Values hash
`values_sha256` SHALL be one hash over sorted rows `[address, actions, sanitized_after, mask]` of every managed resource change that is not `no-op` or `read`, where sensitive or unknown nodes are replaced by `null` in place (structure kept) and `mask` records which nodes were redacted.

#### Scenario: Reordered redacted list element
- **WHEN** a list containing a sensitive element is reordered
- **THEN** the values hash changes

### Requirement: Replan fallback
In binding mode `replan` the system SHALL delete plan files after writing the manifest, re-plan every unit at apply time, and run `ayasee binding verify-replan`, which SHALL refuse with reason `values` unless each unit matches on `(address, actions)` and the values hash and the unit set is equal; only then SHALL the fresh plan be applied.

#### Scenario: Different after values
- **WHEN** the re-plan proposes the same actions with different non-sensitive values
- **THEN** verification fails with `values` and nothing is applied

### Requirement: Mode combinations are checked in one place
`sg_check_mode` SHALL allow `same-job` only in pre-prod stages (never `prod`, `prod-breakglass`, `*-plan` or `drift-*`), require encryption for `encrypted_plan`, allow `encrypt=false` only with `replan` or pre-prod `same-job`, allow `breakglass` only in `prod` with a valid incident reference (`ABC-123` or `INC123`), require encryption off for `drift`, refuse `encrypt=true` with `engine: terraform`, and refuse `only-units` with `breakglass` (the apply action allows it with `same-job` only).

#### Scenario: same-job in prod
- **WHEN** the apply action is called with `binding-mode: same-job` for `prod`
- **THEN** it refuses before planning

### Requirement: Converge check per unit
Every apply mode SHALL converge-check each unit (`plan -detailed-exitcode` must exit 0) before the next unit is applied; a unit that fails or does not converge SHALL stop the run and skip later units.

#### Scenario: Drift after apply
- **WHEN** a unit's re-plan right after apply is not empty
- **THEN** the run stops and later units are not applied

### Requirement: Engine output stays off the job log
Engine output SHALL be written to `$RUNNER_TEMP/ayasee/<env>/logs` and never to the job log.

#### Scenario: Plan with sensitive diff
- **WHEN** a plan runs
- **THEN** the job log contains no engine plan output

### Requirement: Key providers fail closed
`sg_setup_encryption` SHALL build `TF_ENCRYPTION` for exactly one key provider (`azure_vault`, `pbkdf2`, `aws_kms`, `gcp_kms`, `openbao`) and SHALL refuse a missing, malformed or ambiguous key source, including a key reference or credential of another provider, or the test-only `AYASEE_TEST_PASSPHRASE` next to any of them. Provider-specific validation SHALL apply: a versionless Key Vault key URL; a pbkdf2 passphrase of at least 32 characters of `[A-Za-z0-9+/=_.-]`; a KMS key or alias ARN (no bare key id) with a long-term `AKIA` key pair; a `service_account` key file for `gcp_kms` written to a mode-restricted temp file and deleted on exit, refused while Google token or impersonation variables are set; an HTTPS OpenBao transit key URL with a 16–1024 character token, refused with `*_SKIP_VERIFY`.

#### Scenario: Two key sources
- **WHEN** `key_provider` is `pbkdf2` and `AYASEE_KEY_VAULT_KEY_ID` is also set
- **THEN** encryption setup fails as ambiguous

#### Scenario: Temporary AWS key
- **WHEN** `aws_kms` is configured with an `ASIA` access key
- **THEN** encryption setup refuses it

### Requirement: Plan-key secrets reach only plan and apply steps of prod
The reusable workflows SHALL pass the plan-key credentials (`AYASEE_PLAN_PASSPHRASE`, the `AYASEE_KMS_*` pair, `AYASEE_GCP_KMS_CREDENTIALS`, `AYASEE_OPENBAO_TOKEN`) only to the plan step of `prod-plan`, the apply step of `prod`, and the plan and apply steps of `iac-breakglass`.

#### Scenario: Pre-prod stage
- **WHEN** the `dev` stage job runs
- **THEN** none of its steps receives a plan-key credential

### Requirement: Prod refuses cascading changes
`prod-plan` SHALL refuse, with `cascading change: <dependent> depends on <upstream>, which also changes; land <upstream> first`, a plan in which a changed unit reads, through Terragrunt `dependency` blocks directly or transitively, the outputs of another changed unit; `dependencies` blocks SHALL NOT trigger the refusal; an upstream outside the units directory SHALL be an error. `allow_cascade: true` SHALL turn the refusal off and print a warning that it is unsafe.

#### Scenario: Upstream and dependent both change
- **WHEN** unit `grants` depends on `workspace` and both change in one merge
- **THEN** `prod-plan` fails with the cascading-change message

### Requirement: Same-job and break-glass re-plan each unit before applying
In `same-job` and `breakglass` modes the apply action SHALL plan each unit again right before applying it, in dependency order, so dependents see their upstream's new outputs. In `breakglass` mode each fresh plan SHALL propose the same `(address, actions)` set as the policy-checked preview, otherwise the run SHALL stop before that unit with "plan changed since the policy check; re-run break-glass".

#### Scenario: Break-glass plan changed
- **WHEN** a unit's fresh plan in break-glass adds an action the preview did not have
- **THEN** the run stops before applying that unit

### Requirement: A stage without units is explicit
The plan and apply actions SHALL discover units in the main shell, so that a missing units directory, a discovery failure or a duplicate unit name stops the script at once. A units directory that exists but holds no unit SHALL fail with `no units found under <dir>`, unless the plan action's `allow-no-units` input is `true`; the plan action SHALL then plan nothing and succeed with only the outputs `no-units=true` and `has-changes=false`, writing no manifest, change set, summary, plan file or plan JSON. `allow-no-units` SHALL be refused with the `drift` and `breakglass` binding modes and with a value other than `true` or `false`, SHALL never cover a missing units directory, and the apply action SHALL never accept a stage without units. When units exist, the plan action SHALL output `no-units=false`.

#### Scenario: Empty stage without the opt-in
- **WHEN** the plan action runs for a stage whose directory holds only a `.gitkeep`, with `allow-no-units` false
- **THEN** it fails with `no units found under` that directory, before any binding step

#### Scenario: Empty stage with the opt-in
- **WHEN** the plan action runs for that stage with `allow-no-units` true
- **THEN** it succeeds with `no-units=true` and `has-changes=false` and writes no manifest or plan

#### Scenario: Discovery failure with the opt-in
- **WHEN** two unit directories map to the same unit name and `allow-no-units` is true
- **THEN** the plan action fails with `duplicate unit name` and never reports `no-units`

#### Scenario: Missing directory
- **WHEN** `<units_root>/<stage>` does not exist and `allow-no-units` is true
- **THEN** the plan action fails with `units directory not found`

