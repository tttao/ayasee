# Plan binding

Approving a change is only meaningful if what gets applied is what was approved. Plan binding
makes the prod apply use **the exact plan a reviewer saw**, and stop when it cannot prove that.

A stale or mismatched plan **stops the run and asks for a re-plan and a new approval**. It is never
re-planned silently (D1). The implementation is `src/ayasee/binding.py` (the manifest),
`actions/plan/plan.sh` and `actions/apply/apply.sh` (the modes), and `src/ayasee/cascade.py`.

## Why a saved plan

A re-plan after approval that only compares `(address, actions)` misses the changes that matter
most on a Databricks or Azure estate: an approved `update` to `databricks_grants` or
`azurerm_role_assignment` can apply different values and still pass an actions-only check, for
example when a data source returns new group membership. This is the reason for
[decision D1](../governance/decisions.md). Atlantis, tfaction, dflook and ALZ all bind to a saved plan.

Plan files hold sensitive values in cleartext, so a saved plan is only acceptable if it is encrypted
at rest. OpenTofu can do that natively.

## D1: encrypted saved plans plus a manifest

`prod-plan` and `prod` run in binding mode `encrypted_plan`:

1. **Plan.** `prod-plan` plans each unit in dependency order with `-out`. OpenTofu's `plan {}`
   encryption block encrypts every plan file: key provider `azure_vault` (the Key Vault key in
   `AYASEE_KEY_VAULT_KEY_ID`), method `aes_gcm`, `enforced = true`
   (`actions/plan/encryption.tofu.tmpl`). The key URL must be `https://<vault>.vault.azure.net/keys/<name>`
   without a key version, so key rotation does not break a pending approval. Managed HSM keys are
   not supported yet.
2. **Scan inside the job.** The Rego pack and Trivy read the plan JSON in this job. The JSON never
   leaves it.
3. **Manifest.** `ayasee binding make` writes `manifest.json` (schema `ayasee.manifest/v1`):
   the commit SHA, environment, engine and engine version, and per unit the SHA-256 of the plan file
   (`plan_sha256`), the SHA-256 of the provider lock file (`lock_sha256`) and a values hash
   (`values_sha256`, used by the replan fallback).
4. **Upload.** Only `plans/<unit>.tfplan` (encrypted) and `manifest.json` are uploaded, as the
   artifact `ayasee-prod-plan`, kept 7 days.
5. **Approve.** Reviewers see the [risk gate](risk-gate.md) summary, then approve the `prod`
   environment.
6. **Verify, then apply.** `prod` downloads the artifact, runs `terragrunt init` in each unit to get
   its current provider lock file, and runs `ayasee binding verify`. The checks run in this order
   and the first failure stops the run:

   | Order | Check | Reason code |
   |---|---|---|
   | 1 | The manifest SHA equals the commit being applied | `sha` |
   | 2 | The engine version equals the one that made the plan | `engine` |
   | 3 | The set of units equals the set in the manifest | `unit_set` |
   | 4 | Each plan file's SHA-256 equals the manifest | `plan_hash` |
   | 5 | Each unit's provider lock hash equals the manifest | `lock_hash` |

   A mismatch exits with code 3 and the run stops with "approved plan no longer matches: stop,
   re-plan and re-approve (nothing was applied)". Any other failure exits 1. Nothing has been
   applied in either case.
7. **Engine staleness check.** Applying a saved plan also fails when the state changed since the plan
   was made (the state serial moved), so an old plan cannot be replayed against newer state. The
   engine's own refusal is what stops a state change between approval and apply.

A binding or stale-plan failure in prod is **not** an incident: it opens no stop-the-line issue
(R34). The job summary and the `prod_failed` card say to re-run `prod-plan` and approve again.

The provider lock hash matters because the Databricks provider releases weekly, and a minor release
can change plans. Commit `.terraform.lock.hcl` for every unit so `prod-plan` and `prod` resolve the
same provider versions.

## R16/R17: the values hash

The manifest also carries `values_sha256` per unit. It is one hash over the sorted rows
`[address, actions, sanitized_after, mask]` of every managed resource change that is not `no-op` or
`read`:

- `sanitized_after` is the planned `after` value with every node marked sensitive or unknown
  replaced by `null` **in place**. Structure is kept (R17), so a reorder, an insertion or a removal
  involving a redacted list element still changes the hash.
- `mask` records which nodes were redacted, so redacting a value is not the same as it being `null`.
- Rows are sorted canonically, so the order of resource changes does not matter.

The hash is computed from plan JSON in `show -json` format (`format_version` 1.x), and sensitive or
unknown values never enter it. The known limit: a change that touches only sensitive or unknown
paths does not change the hash.

## The replan fallback

For engines that cannot encrypt plans (plain Terraform, D6), binding mode `replan` implements
D1(b):

1. `prod-plan` makes the manifest, then **deletes the plan files** (encryption is off, and a replan
   run never needs them). Only the manifest is kept.
2. `prod` plans every unit again, reads the new plan as JSON and runs
   `ayasee binding verify-replan`.
3. The re-plan must match on `(address, actions)` **and** on the hash of the non-sensitive `after`
   values, per unit. Any difference stops the run with reason `values`, as does a different set of
   units.
4. Only then is the fresh plan applied.

The check is stricter than comparing actions and weaker than a saved plan. The values hash cannot see
sensitive or unknown values.

**Status.** `replan` is implemented in `actions/plan`, `actions/apply` and the `ayasee binding`
CLI and is tested there. `iac-staged` does not select it yet: its `prod-plan` and `prod` jobs pass
`binding-mode: encrypted_plan` and `encrypt: "true"`, and the plan action refuses
`encrypt=true` with `engine: terraform` ("encrypt=true needs engine opentofu"). A repository that
sets `engine: terraform` therefore fails at `prod-plan` when called through `iac-staged` today.
A subset check (re-plan and compare only the actions) is not implemented.

## What each mode allows (R29)

`sg_check_mode` in `actions/lib/ayasee-lib.sh` is the one place that allows or refuses a
combination.

| Mode | Environments | Encryption | What it does | Verification before apply |
|---|---|---|---|---|
| `same-job` | `dev`, `uat` only | off (the plan never leaves the job) | Plans every unit as a preview, then applies unit by unit: plan again, apply, converge-check | None, the plan was made in this job |
| `encrypted_plan` | any, used for `prod` | required (`encrypt=true`, OpenTofu) | `prod-plan` plans and encrypts; `prod` applies the approved files | Manifest: SHA, engine, units, plan hash, lock hash |
| `replan` | any, secondary profile | may be off; plan files are deleted after the manifest | `prod` re-plans, compares, applies the fresh plan | `(address, actions)` plus values hash per unit |
| `breakglass` | `prod` only, and only with a valid incident reference (`ABC-123` or `INC123`) | on in `iac-breakglass` | Plan, policy pack, apply in one job, like `same-job` | The plan checked by the policy pack must equal the plan applied (R53, below) |
| `drift` | nightly drift only | must be off | `plan -detailed-exitcode` per unit; plan files are deleted, never applied | None, nothing is applied |

Other rules that follow from R29:

- `encrypted_plan` without encryption is refused. `encrypt=false` is only allowed with `replan`, or
  with `same-job` in `dev` and `uat`.
- `same-job` outside `dev` and `uat` is refused. `breakglass` outside `prod` is refused.
- `engine: terraform` cannot encrypt, so it needs `encrypt=false`, and therefore `replan`.
- `replan` deletes plaintext plan files and never exposes the plan directory.
- Engine output goes to `$RUNNER_TEMP/ayasee/<env>/logs` and never to the job log.
- Every unit is converge-checked (a `plan -detailed-exitcode` that must exit 0) before the next
  unit is applied. A unit that fails or does not converge stops the run and later units are skipped.
- `only-units` limits a run to named units. The apply action allows it with `same-job` only, and
  the plan action refuses it with `breakglass`: break-glass always plans and applies the whole prod
  tree (R71).
- The test-only variable `AYASEE_TEST_PASSPHRASE` (a PBKDF2 passphrase) replaces Key Vault in
  tests. Setting it together with a key URL is an error.

## The cascade rule (R52, R53)

A unit that reads another unit's outputs through a Terragrunt `dependency` block is planned with the
outputs its upstream has **before** the upstream is applied. If both units change, the saved plan of
the dependent is wrong the moment the upstream is applied.

**R52(a): same-job and breakglass re-plan each unit right before applying it.** The plan made by the
plan action is a preview. The apply action then works one unit at a time in dependency order: plan,
apply, converge-check. Every dependent is planned with the upstream's new outputs. Dev and uat are
therefore safe.

**R52(b): prod refuses a cascade.** `encrypted_plan` and `replan` cannot remake the approved plan, so
`prod-plan` refuses when a unit with changes depends, directly or through other units, on another
unit with changes in the same plan:

```
cascading change: <dependent> depends on <upstream>, which also changes; land <upstream> first
```

Dependencies are read with `terragrunt render --json` (`sg_edges`). Only `dependency` blocks count,
because outputs flow through them. A `dependencies` block only orders units and does not trigger the
refusal. An upstream outside the units directory is an error, never silently dropped.

Land the upstream unit in its own merge, wait until it is live in prod, then merge the dependent.
First-time adoption of dependent units follows the same path, see
[the example](../../examples/databricks-azure/README.md#7-first-merge).

`allow_cascade: true` in `.ayasee.yaml` switches the refusal off. It is **unsafe**: the converge
check after each apply still catches a dependent that no longer matches the code, but only after the
upstream was applied. The plan action prints "allow-cascade is true: cascading changes are not refused (unsafe)" when it is set.

**R53: break-glass rechecks the plan it applies.** Break-glass reviewers approve before a plan
exists, so the policy pack is the machine check, and it runs on the preview plan. Because R52(a) makes
the apply plan fresh, each unit's fresh plan must propose the same `(address, actions)` set as the
checked preview. Otherwise the run stops before that unit's apply with "plan changed since the policy
check; re-run break-glass".

## Supersession is a separate control

Binding proves the plan is the approved one. It does not prove the plan is the newest. A run that was
approved while newer commits reached uat is rejected by the guard at apply time as `superseded`. See
[the supersession guard](staged-run.md#the-supersession-guard).
