# azure-stage-identity

One user-assigned managed identity (`ayasee-<repo>-<environment>`) and one federated credential
(audience `api://AzureADTokenExchange`) per GitHub environment. Call it once per environment:
`dev-plan`, `drift-<stage>`, each stage, `prod-plan`, `prod`, `prod-breakglass`. Never share an
identity between environments. Provider: `azurerm`.

Outputs: `client_id` (set it as `AZURE_CLIENT_ID` on the environment), `principal_id`, `subject`.

## Subject

Default subject: `repo:<owner>/<repo>:environment:<environment>`.

**Immutable `sub` trigger.** Repositories that are created, renamed or transferred on or after
2026-07-15 get GitHub's immutable subject format, built from the owner and repository IDs:
`repo:<org>@<id>/<repo>@<id>:environment:<env>`. The `subject` input exists for exactly that case:
pass the exact `sub` your workflow tokens carry (read it from a token, or from the repository's OIDC
settings). Older repositories keep the name-based default until they are renamed or transferred.

**The `sub` carries no workflow identity.** The environment-based subject names the repository and
the environment, not the workflow file: standard Entra federated credentials match `sub` exactly
and cannot match on `job_workflow_ref`. Any workflow of the repository that runs in the environment
can therefore use its identity. The real boundary is the environment itself: the main-only branch
policy and, for `prod` and `prod-breakglass`, the required reviewers (both set by the
`github-environments` module).

Repository names with characters an Azure identity name does not allow (for example `.` in
`acme/my.repo`) are written with `-` in the identity and credential names
(`ayasee-my-repo-<environment>`); the subject keeps the real name.

For GHES, set `issuer` to the `oidc_issuer` that `ayasee features --json` prints for your
instance (`https://HOST/_services/token`); the default is the github.com issuer
`https://token.actions.githubusercontent.com`.

## Role assignments (not created here)

They depend on your estate, so assign them yourself with `principal_id`. Minimum per environment:

| Environment | Minimum roles |
|---|---|
| `dev-plan` | Reader; Storage Blob Data Reader on the dev state container (PR plans run with `-lock=false`). No apply rights, no ledger access |
| `dev`, `uat` | Apply rights on that stage's estate; Storage Blob Data Contributor on that stage's state container (state and lock lease); write (Storage Blob Data Contributor) on the ledger |
| `prod-plan` | Reader on prod; Storage Blob Data Contributor on the prod state container (state read and the lock lease); `wrapKey` **and** `unwrapKey` on the Key Vault key (`AYASEE_KEY_VAULT_KEY_ID`): it encrypts its plan and then runs `tofu show -json` on that encrypted plan, which decrypts it; write on the prod ledger record (the `prod-in-sync` job, R31). Cannot apply |
| `prod` | Apply rights on prod; Storage Blob Data Contributor on the prod state container; `unwrapKey` on the same key; read and write on the ledger |
| `prod-breakglass` | Its own identity, never the `prod` one: prod apply rights; Storage Blob Data Contributor on the prod state container; `wrapKey` and `unwrapKey` on the key (it plans and applies in one job); read and write on the ledger |
| `prod-pr-plan` (only with `shared_units`) | Reader on the shared prod resources; Storage Blob Data Reader on the prod state container (PR plans run with `-lock=false`). No apply rights, no key access, no ledger access. It runs pull-request code: see [shared units](../../docs/concepts/pr-checks.md#shared-units) |
| `drift-<env>` | Reader; Storage Blob Data Contributor on that env's state container (needed for the state lease); read on the ledger. No apply rights |

Test: `tofu init -backend=false && tofu test`.
