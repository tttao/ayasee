# `.ayasee.yaml` reference

`ayasee` reads `.ayasee.yaml` from the repository root. The file is optional: when it
is missing or empty, every key takes its default. Validation is strict and fails closed. An
unknown key at any level, a value of the wrong type or an invalid literal is a
configuration error (`ayasee config show` exits 1 with a message).

Print the resolved configuration, including the derived `binding_mode`, as JSON:

```bash
ayasee config show [--path FILE]
ayasee config check-supported [--path FILE]
ayasee config staged [--path FILE] [--start-stage STAGE]
ayasee config labels [--path FILE]
ayasee config check-shared [--path FILE]
```

`labels` prints one line per opt-in that weakens a compliant default (`solo_mode`,
`identity: secrets`); the reusable workflows turn each line into a warning and a line of the run
summary.

`staged` prints how `iac-staged` maps `stages` onto its jobs, as `key=value` lines for
`$GITHUB_OUTPUT` (`run-dev`, `run-uat`, `first-stage`, `pre-prod-stage`, `order`), or exits 1 with
a configuration error for a stage list or `start-stage` that `iac-staged` cannot run.

`check-supported` exits 1 and prints one line per setting that the parser accepts but no workflow
implements yet (R35, R73): `gate: risky`, `engine: terraform`, `terragrunt: false`, a non-empty
`extra_rules` and `notify.teams: false`. Each line starts with `<key>: not supported yet` (for
`gate`, "gate: risky is not supported yet"). The first job of `iac-staged`, `iac-pr` (`plan-dev`),
`iac-nightly` and the `iac-breakglass` job run it before any cloud login and fail the run on any
such setting, so it never silently runs something else.

`check-shared` exits 1 and prints one line per problem when `shared_units` does not match the units
on disk: a name with no unit under `<units_root>/prod`, or one that also exists under another stage.
Run it from the repository root.

## Keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `stages` | list of strings | `[dev, uat, prod]` | Promotion order. Names must be unique, the list must be non-empty and it must contain `prod`. `iac-staged` accepts two or three stages with `prod` last, `[<pre-prod>, prod]` or `[<first>, <pre-prod>, prod]`, and fails the run on any other list (`ayasee config staged`; pre-prod names match `[a-z0-9][a-z0-9_-]*` and are not `prod-plan`, `prod-breakglass`, `dev-plan` or `drift-*`); see [the staged run](../concepts/staged-run.md#stages). `iac-pr` plans the first stage, `iac-nightly` drifts every listed stage and the cockpit reads its own `stages` from `repos.yaml`. |
| `engine` | `opentofu` or `terraform` | `opentofu` | IaC engine. `opentofu` binds prod to the exact approved encrypted plan (D1). `terraform` selects the replan fallback, which compares `(address, actions)` plus a hash of non-sensitive `after` values (D1b), in the actions and the CLI. **Not supported by the workflows yet**: every reusable workflow refuses it in its first job with "engine: not supported yet" (R73). See [plan binding](../concepts/plan-binding.md#the-replan-fallback). |
| `terragrunt` | boolean | `true` | Units are always discovered and driven through Terragrunt. `false` (plain OpenTofu layouts) is **not supported yet**: the workflows refuse it with "terragrunt: not supported yet" (R73). |
| `iac_root` | string | `.` | Directory, relative to the repository root, that holds the estate: `root.hcl`, the modules and `units_root`. Set it in a monorepo where the IaC sits beside application code (for example `infra/iac`). The PR and nightly format checks, the Terragrunt validation, Trivy and Checkov are scoped to it, and `changed-units` ignores files outside it (except the configuration file), so an application-only pull request plans nothing. Checkov's `github_actions` framework runs only when `iac_root` is `.`. It must be relative and stay inside the repository. See [monorepos](../concepts/pr-checks.md#monorepos). |
| `units_root` | string | `stages` | Directory that contains the stage units, relative to the repository root (not to `iac_root`). It must be inside `iac_root`: with `iac_root: infra/iac`, write `units_root: infra/iac/stages`. |
| `shared_units` | list of strings | `[]` | Prod units that exist once and serve every stage (a tunnel ingress, an access application, a DNS zone). Names as the plan action writes them: the path below `<units_root>/prod`, with `/` written `__`. Each must exist under `prod` and under no other stage (`ayasee config check-shared`, run by `iac-pr` and `iac-staged`). They are applied behind the prod gate, flagged as shared in the gate summary and the change set, and planned read-only in pull requests through the `prod-pr-plan` environment. See [units that serve every stage](../concepts/staged-run.md#units-that-serve-every-stage). |
| `ledger.url` | string or null | `null` | Ledger store URL: `file://`, `git-branch://` or `azure-blob://` ([the ledger](../concepts/ledger.md#stores-and-urls)). The `AYASEE_LEDGER_URL` variable of a GitHub environment takes precedence. |
| `gate` | `always` or `risky` | `always` | Prod approval gate. `always` requires approval on every prod run (D9). `risky` is accepted by the parser but **not supported**: every reusable workflow rejects it with "gate: risky is not supported yet" (R35). |
| `solo_mode` | boolean | `false` | Opt-in single-maintainer mode. **NON-COMPLIANT**: it disables independent approval. Every load prints `NON-COMPLIANT: solo_mode disables independent approval (DORA RTS 2024/1774 Art. 17(1)(b))` to stderr (D2), so it appears in the log of every run. The key does not itself change any environment setting; the independent-approval controls are the `prod` environment's reviewers and `prevent_self_review`, which the `github-environments` module relaxes only with its own `solo_mode = true` ([solo mode](../compliance/dora-art17.md#solo-mode-is-non-compliant)). The cockpit tags such a repository. |
| `allow_cascade` | boolean | `false` | **UNSAFE.** `prod-plan` refuses a plan in which a unit with changes depends (through a `dependency` block, directly or transitively) on another unit with changes, because the saved plan of the dependent was made against the upstream's old outputs (R52). `true` disables that refusal; the converge check after each apply then catches a dependent that no longer matches the code, but only after the upstream was applied. Land the upstream unit in its own merge instead. |
| `extra_rules` | list of strings | `[]` | Extra risk-rule files, merged after the packaged rules; a file can only raise a tier (see [the risk gate](../concepts/risk-gate.md#rule-files)). Works with `ayasee changeset --rules` and the `rules` input of the `risk` action. A non-empty list is **not supported by the workflows yet**: they refuse it with "extra_rules: not supported yet" rather than ignore it (R73). |
| `drift_noise` | map of string to list of strings | `{}` | Resource type to attribute paths ignored when judging drift. Read by `iac-nightly` at the live SHA ([drift](../concepts/drift.md#what-counts-as-drift)). |
| `identity` | `oidc` or `secrets` | `oidc` | How stage jobs reach the cloud (D7). `oidc` is the Entra OIDC profile: `azure/login` and the `ARM_*` variables, one managed identity per environment, no stored cloud secret. `secrets` skips `azure/login` and leaves `ARM_*` and `DATABRICKS_AUTH_TYPE` empty, for providers that cannot federate with GitHub OIDC; the plan and apply steps then receive the provider tokens `CLOUDFLARE_API_TOKEN`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, which must be environment secrets, one set per environment (see [identity: secrets](../concepts/staged-run.md#identity-secrets)). **DEVIATION**: every load prints `DEVIATION: identity: secrets stores provider API tokens as GitHub environment secrets instead of OIDC federation (D7); stages are isolated only as far as the token scopes are (a pre-prod token whose scope covers prod objects, e.g. a shared DNS zone, can change them without the prod gate)` to stderr, and the first job of every reusable workflow repeats it as a warning and in the run summary. |
| `plan_encryption.key_provider` | `azure_vault` or `pbkdf2` | `azure_vault` | Where the prod plan encryption key comes from (D1). `azure_vault` is the Key Vault key in the `AYASEE_KEY_VAULT_KEY_ID` variable. `pbkdf2` derives it from the `AYASEE_PLAN_PASSPHRASE` secret, which must be an environment secret of `prod-plan`, `prod` and `prod-breakglass` only. The manifest and the stale-plan checks do not change. `openbao`, `aws_kms` and `gcp_kms` are refused (not wired yet). See [key providers](../concepts/plan-binding.md#key-providers). |
| `notify.teams` | boolean | `true` | A Teams card is sent whenever the `teams-webhook` secret is set, and skipped otherwise. `false` is **not supported yet** (the workflows refuse it with "notify.teams: not supported yet", R73): to turn Teams off, leave the secret unset. |

### Derived value

`binding_mode` is read-only and cannot be set in the file: `encrypted_plan` for
`engine: opentofu`, `replan` for `engine: terraform`. It appears in the output of
`ayasee config show`.

## Full example

```yaml
stages: [dev, uat, prod]
engine: opentofu
terragrunt: true
iac_root: .
units_root: stages
ledger:
  url: azure-blob://account/container/ledger
gate: always
solo_mode: false
identity: oidc
plan_encryption:
  key_provider: azure_vault
allow_cascade: false
extra_rules:
  - rules/team-rules.yaml
shared_units: []
drift_noise:
  databricks_job:
    - email_notifications
    - tags.last_touched_by
notify:
  teams: true
```
