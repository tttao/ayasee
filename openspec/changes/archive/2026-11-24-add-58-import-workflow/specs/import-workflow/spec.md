## ADDED Requirements

### Requirement: Import run on a branch, first stage only
The reusable workflow `iac-import` SHALL run only for a `workflow_dispatch` on a branch (`refs/heads/*`). It SHALL accept a `stage` input that is empty or the first configured stage and a `unit` input that is empty (every unit of the stage) or a relative path of plain names whose `terragrunt.hcl` exists under `<units_root>/<stage>`; any other stage SHALL be refused before any environment or credential is used, with a message that names the local `ayasee import-plan` CLI. Inputs SHALL reach scripts through environment variables only.

#### Scenario: Prod stage asked
- **WHEN** `iac-import` is dispatched with `stage: prod` and stages `[test, prod]`
- **THEN** the `resolve` job fails with a message naming the first stage and the local CLI, and no job runs in an environment

#### Scenario: Unit escaping the units root
- **WHEN** `unit` is `../prod/dns`
- **THEN** `resolve` refuses it and nothing is planned

### Requirement: Plan read-only in the pull-request plan environment
The import job SHALL run in the `<environment_prefix><first stage>-plan` environment named by `resolve`, with `contents: read` and `id-token: write` only, check out the dispatched commit without persisted credentials, refuse unsupported settings, and plan with the plan action using `binding-mode: same-job`, `encrypt: false` and `lock: false`. With `identity: secrets` the provider tokens and `AYASEE_PROVIDER_ENV` SHALL reach the plan step only, and `CLOUDFLARE_API_TOKEN` SHALL also reach the `import-plan` step, and no other step.

#### Scenario: Secrets identity
- **WHEN** the configuration has `identity: secrets`
- **THEN** only the plan step and the `import-plan` step receive a secret, and the `import-plan` step receives `CLOUDFLARE_API_TOKEN` alone

### Requirement: No plan value leaves the import job
The import job SHALL delete the plan files, the plan JSON and the engine logs (`$RUNNER_TEMP/ayasee`) in a step that runs whatever happened before, SHALL never upload them, and SHALL upload only the artifact `ayasee-imports` holding each written `imports.tf` at its repository path and a summary that lists each planned create by address with its outcome (`import`, `create`, `skip`, `ambiguous`). It SHALL never commit or push.

#### Scenario: Sentinel value in the plan
- **WHEN** a planned create carries a sentinel value in an attribute
- **THEN** neither the summary nor any `imports.tf` contains it

#### Scenario: Provider API error
- **WHEN** a lookup fails
- **THEN** no `imports.tf` and no summary are written, the job fails, and the plan files are still deleted

### Requirement: Comment on the branch's pull request without branch code
When the `comment` input is true, a separate `comment` job with `contents: read` and `pull-requests: write` only, no OIDC token and no checkout of the dispatched branch (it checks out the default branch), SHALL post the summary as the sticky comment marked `import-plan` on the open pull request whose head is the dispatched branch of this repository, and SHALL post nothing when there is none.

#### Scenario: No pull request yet
- **WHEN** the dispatched branch has no open pull request
- **THEN** the comment job says so and succeeds; the artifact still holds the result
