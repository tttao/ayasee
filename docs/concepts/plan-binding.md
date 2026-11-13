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
   encryption block encrypts every plan file with method `aes_gcm` and `enforced = true`. The key
   comes from the key provider set in `plan_encryption.key_provider` (see
   [key providers](#key-providers)): by default `azure_vault`, the Key Vault key in
   `AYASEE_KEY_VAULT_KEY_ID` (`actions/plan/encryption.tofu.tmpl`).
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

## Key providers

`plan_encryption.key_provider` in `.ayasee.yaml` picks where the plan key comes from. The
encryption method, the manifest and the stale-plan checks are the same for every provider: only
the key source changes. `sg_setup_encryption` in `actions/lib/ayasee-lib.sh` builds the
`TF_ENCRYPTION` block and fails closed on a missing, malformed or ambiguous key source.

| Provider | Key source | Who can decrypt |
|---|---|---|
| `azure_vault` (default) | The Key Vault key in the `AYASEE_KEY_VAULT_KEY_ID` variable, `https://<vault>.vault.azure.net/keys/<name>` without a key version (so rotation does not break a pending approval). Managed HSM keys are not supported yet. | The identities with `unwrapKey` on that key: `prod-plan`, `prod`, `prod-breakglass` |
| `pbkdf2` | The passphrase in the `AYASEE_PLAN_PASSPHRASE` secret, at least 32 characters of `[A-Za-z0-9+/=_.-]` (for example `openssl rand -base64 48`), turned into the key by OpenTofu's `pbkdf2` provider. | The environments that hold the secret. Create it as an environment secret of `prod-plan`, `prod` and `prod-breakglass` only. |
| `aws_kms` | The AWS KMS key in the `AYASEE_KMS_KEY_ID` variable: a key ARN `arn:aws:kms:<region>:<account>:key/<id>` (multi-Region `mrk-` keys included) or an alias ARN `arn:aws:kms:<region>:<account>:alias/<name>`; the region is taken from the ARN, a bare key id is refused. OpenTofu's `aws_kms` provider asks KMS for an `AES_256` data key with the encryption context `ayasee = plan`, using the IAM key pair in the `AYASEE_KMS_ACCESS_KEY_ID` and `AYASEE_KMS_SECRET_ACCESS_KEY` secrets. Works with either `identity`. Needs OpenTofu 1.13 or later (the default pin of the setup action, 1.13.9): older versions reject `encryption_context`. | The environments that hold the key pair. Create both as environment secrets of `prod-plan`, `prod` and `prod-breakglass` only. |
| `gcp_kms` | The Cloud KMS key in the `AYASEE_GCP_KMS_KEY_NAME` variable, `projects/<project>/locations/<location>/keyRings/<ring>/cryptoKeys/<key>` without a key version (so rotation does not break a pending approval). OpenTofu's `gcp_kms` provider has KMS encrypt a fresh 32-byte data key, using the service-account key file in the `AYASEE_GCP_KMS_CREDENTIALS` secret. Works with either `identity`. | The environments that hold the key file. Create it as an environment secret of `prod-plan`, `prod` and `prod-breakglass` only. |
| `openbao` | The OpenBao transit key in the `AYASEE_OPENBAO_KEY_URL` variable, `https://<host>[:<port>]/v1/<transit mount>/keys/<name>` (the key's own API URL; the mount may have several segments). OpenTofu's `openbao` provider asks the transit engine for a 32-byte data key, using the token in the `AYASEE_OPENBAO_TOKEN` secret. Works with either `identity`. HTTPS only, and the runner must trust the server certificate. | The environments that hold the token. Create it as an environment secret of `prod-plan`, `prod` and `prod-breakglass` only. |

With `pbkdf2`, the reusable workflows pass `AYASEE_PLAN_PASSPHRASE` to the plan step of `prod-plan`,
the apply step of `prod` and the plan and apply steps of `iac-breakglass`, and to no other step.
The caller passes it by name (`AYASEE_PLAN_PASSPHRASE: ${{ secrets.AYASEE_PLAN_PASSPHRASE }}`);
according to GitHub's documentation each job then receives the value of the environment secret of
its own environment, which takes precedence over a repository secret of the same name (not yet
verified by a run). A repository or organisation secret of that name would reach every job that is
passed it, so do not create one; the workflows cannot detect it. The passphrase is a stored secret,
unlike the Key Vault key, which no job ever holds: anyone who can read it can decrypt an uploaded
plan artifact during its 7 days.

With `aws_kms` (issue #10), KMS is reached with a dedicated key pair, never with the AWS
credentials of the stage (with `identity: secrets` those are often the R2 or other S3-compatible
keys of the state backend, which cannot call KMS). The reusable workflows pass
`AYASEE_KMS_ACCESS_KEY_ID` and `AYASEE_KMS_SECRET_ACCESS_KEY` to the same four steps as the pbkdf2
passphrase (the plan step of `prod-plan`, the apply step of `prod`, the plan and apply steps of
`iac-breakglass`) and to no other step; pass them by name and create them as environment secrets
of `prod-plan`, `prod` and `prod-breakglass` only, with the same caveat about repository-level
secrets as above. `sg_setup_encryption` accepts only a long-term IAM user key (`AKIA` and 16 of
`[A-Z0-9]`, with a secret of 40 characters of `[A-Za-z0-9/+]`): a temporary `ASIA` key needs a
session token and expires, which a stored secret cannot follow. The pair is written into the
`key_provider` block (`access_key`, `secret_key`) of `TF_ENCRYPTION`, so the stage's own AWS
variables play no part. No ayasee message prints it, OpenTofu shows `(source code not available)`
instead of a `TF_ENCRYPTION` line, and GitHub masks it in logs because the workflows take it from
the `secrets` context.

Give that IAM user nothing but `kms:GenerateDataKey` and `kms:Decrypt` on the one key, and let the
key policy allow those two actions to that user only, with the condition
`"StringEquals": {"kms:EncryptionContext:ayasee": "plan"}` so the key serves nothing but ayasee
plans. Grant both actions: `prod-plan` encrypts and then decrypts its own plan for
`tofu show -json`, and whether an apply alone gets by with `kms:Decrypt` has not been checked. As
with `pbkdf2`, the key pair is a stored secret: anyone who can read it can decrypt an uploaded plan
artifact during its 7 days, until the key is deactivated or the IAM key is deleted, and each use
is logged in CloudTrail. Use a key ARN rather than an alias ARN if you can: pointing the alias at
another key while a plan waits for approval makes the apply fail to decrypt it, and the run has to
be planned again.

What has been tested for `aws_kms`: the rendered `TF_ENCRYPTION` block, the ARN and key-pair
checks and the refusals (`tests/actions/test_lib_encryption.py`), and that OpenTofu 1.13 accepts
the block and makes its first AWS call (STS `GetCallerIdentity`, sent in the test to a closed local
port, with no AWS configuration of the developer in reach). A plan encrypted and decrypted through
a real KMS key has not been run: the pbkdf2 integration test covers the rest of the path, which is
the same for every provider.

With `gcp_kms` and `openbao` (issue #10), the key service is reached the same way: with a
dedicated prod-only credential, never with the credentials of the stage. The reusable workflows pass
`AYASEE_GCP_KMS_CREDENTIALS` or `AYASEE_OPENBAO_TOKEN` to the same four steps as the other plan-key
secrets and to no other step; pass them by name and create them as environment secrets of
`prod-plan`, `prod` and `prod-breakglass` only, with the same caveat about repository-level secrets.
`ayasee doctor` checks that the secret is passed, that it is an environment secret of those three
environments and that the key variable is set on them.

- **`gcp_kms`.** `AYASEE_GCP_KMS_CREDENTIALS` holds the JSON key file of a service account, as
  downloaded. `sg_setup_encryption` accepts only a service-account key (`"type": "service_account"`,
  a private key, a `...iam.gserviceaccount.com` client email, the default token URI): an
  `external_account` or `authorized_user` file is refused, since the first fetches a token from a
  URL or command of its own. A multi-line key file cannot be checked against a safe character set,
  so it never goes into `TF_ENCRYPTION`: it is written to a new file readable only by the runner
  user under `RUNNER_TEMP`, `TF_ENCRYPTION` names that file in `credentials`, the secret is removed
  from the environment, and the file is deleted when the plan or apply script exits (a runner killed
  mid-step leaves it to the end-of-job cleanup of `RUNNER_TEMP`). Give the service account only
  the Cloud KMS CryptoKey Encrypter/Decrypter role (`roles/cloudkms.cryptoKeyEncrypterDecrypter`),
  granted on the one key, not on the key ring or the project. OpenTofu would prefer
  `GOOGLE_OAUTH_ACCESS_TOKEN` (and possibly an impersonation variable) of the step to that file, so
  `sg_setup_encryption` refuses `gcp_kms` while `GOOGLE_OAUTH_ACCESS_TOKEN`,
  `GOOGLE_IMPERSONATE_SERVICE_ACCOUNT` or `GOOGLE_BACKEND_IMPERSONATE_SERVICE_ACCOUNT` is set in the
  plan or apply step. Organisation policies often forbid
  service-account keys (`iam.disableServiceAccountKeyCreation`); Workload Identity Federation with
  the GitHub OIDC token is not wired yet.
- **`openbao`.** `AYASEE_OPENBAO_TOKEN` is rendered into the `key_provider` block (`token`) like the
  `aws_kms` key pair and removed from the environment, so a `BAO_TOKEN` or `VAULT_TOKEN` of the
  stage plays no part; it must be 16 to 1024 characters of `[A-Za-z0-9._-]`. Give the token a
  policy with only `update` on `<transit mount>/datakey/plaintext/<key>` and
  `<transit mount>/decrypt/<key>`. OpenTofu does not renew the token, so a stored token has to
  outlive the time a plan waits for approval: use a token with an explicit long TTL (or a periodic
  token renewed elsewhere) and rotate the secret before it expires; an expired token fails the apply,
  and the run has to be planned again. A JWT login with the GitHub OIDC token is not wired yet.
  OpenBao namespaces are not supported, and `BAO_SKIP_VERIFY` or `VAULT_SKIP_VERIFY` in the plan or
  apply step is refused.

What has been tested for `gcp_kms` and `openbao`: the rendered blocks, the key-name, key-URL,
key-file and token checks, the refusals and the clean-up of the key file
(`tests/actions/test_lib_encryption.py`), and that OpenTofu 1.13 accepts each block and makes its
first call to the key service: for `gcp_kms` after loading the throwaway service-account key from
the file (stopped by a proxy on a closed local port), for `openbao` to
`<address>/v1/<transit mount>/datakey/plaintext/<key>` (a closed local port). No plan has been
encrypted and decrypted through a real Cloud KMS key or OpenBao server.

Any second key source is refused as ambiguous: a key reference (`AYASEE_KEY_VAULT_KEY_ID`,
`AYASEE_KMS_KEY_ID`, `AYASEE_GCP_KMS_KEY_NAME`, `AYASEE_OPENBAO_KEY_URL`) or key credential
(`AYASEE_PLAN_PASSPHRASE`, the `AYASEE_KMS_*` key pair, `AYASEE_GCP_KMS_CREDENTIALS`,
`AYASEE_OPENBAO_TOKEN`) of a provider other than the chosen one, or the test-only
`AYASEE_TEST_PASSPHRASE` next to any of them.

## What each mode allows (R29)

`sg_check_mode` in `actions/lib/ayasee-lib.sh` is the one place that allows or refuses a
combination.

| Mode | Environments | Encryption | What it does | Verification before apply |
|---|---|---|---|---|
| `same-job` | pre-prod stages only (never `prod`, `prod-breakglass`, `*-plan` or `drift-*`) | off (the plan never leaves the job) | Plans every unit as a preview, then applies unit by unit: plan again, apply, converge-check | None, the plan was made in this job |
| `encrypted_plan` | any, used for `prod` | required (`encrypt=true`, OpenTofu) | `prod-plan` plans and encrypts; `prod` applies the approved files | Manifest: SHA, engine, units, plan hash, lock hash |
| `replan` | any, secondary profile | may be off; plan files are deleted after the manifest | `prod` re-plans, compares, applies the fresh plan | `(address, actions)` plus values hash per unit |
| `breakglass` | `prod` only, and only with a valid incident reference (`ABC-123` or `INC123`) | on in `iac-breakglass` | Plan, policy pack, apply in one job, like `same-job` | The plan checked by the policy pack must equal the plan applied (R53, below) |
| `drift` | nightly drift only | must be off | `plan -detailed-exitcode` per unit; plan files are deleted, never applied | None, nothing is applied |

Other rules that follow from R29:

- `encrypted_plan` without encryption is refused. `encrypt=false` is only allowed with `replan`, or
  with `same-job` in a pre-prod stage.
- `same-job` in `prod` or a fixed-role environment is refused. `breakglass` outside `prod` is refused.
- `engine: terraform` cannot encrypt, so it needs `encrypt=false`, and therefore `replan`.
- `replan` deletes plaintext plan files and never exposes the plan directory.
- Engine output goes to `$RUNNER_TEMP/ayasee/<env>/logs` and never to the job log.
- Every unit is converge-checked (a `plan -detailed-exitcode` that must exit 0) before the next
  unit is applied. A unit that fails or does not converge stops the run and later units are skipped.
- `only-units` limits a run to named units. The apply action allows it with `same-job` only, and
  the plan action refuses it with `breakglass`: break-glass always plans and applies the whole prod
  tree (R71).
- The test-only variable `AYASEE_TEST_PASSPHRASE` (a PBKDF2 passphrase) replaces the key provider
  in tests. Setting it together with a key URL or `AYASEE_PLAN_PASSPHRASE` is an error.

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
