# The staged run

`iac-staged` is a reusable workflow. A repository calls it once, on push to `main`, and one run
carries a commit through every stage: **dev, then uat, then a prod plan, then the gated prod
apply**. Nothing is re-planned silently between stages and nothing leaves a job unencrypted.

```yaml
# .github/workflows/iac.yml in the adopting repository
on:
  push:
    branches: [main]
jobs:
  iac:
    uses: <owner>/ayasee/.github/workflows/iac-staged.yml@<sha>
    with:
      start-stage: ""             # empty (first stage) | a pre-prod stage | prod-plan
    secrets:
      teams-webhook: ${{ secrets.TEAMS_WEBHOOK }}   # optional
      ledger-token: ${{ secrets.LEDGER_TOKEN }}     # optional; git-branch:// ledger store only
    # never `secrets: inherit`
```

## Stages

The run promotes through the `stages` of `.ayasee.yaml`: one to four pre-prod stages, then
`prod`. `iac-staged` has four pre-prod job slots, `stage-1` to `stage-4`, each needing the one
before it. The pre-prod stages fill them right-aligned, so `stage-4` always runs the stage just
before prod and the unused slots come first, skipped. `resolve` refuses any other list
(`ayasee config staged`):

| `stages` | `stage-1` | `stage-2` | `stage-3` | `stage-4` | Guard of `prod-plan` and `prod` chains from |
|---|---|---|---|---|---|
| `[<a>, prod]` | skipped | skipped | skipped | `<a>` | `<a>` |
| `[dev, uat, prod]` (default), or any `[<a>, <b>, prod]` | skipped | skipped | `<a>` | `<b>` | `<b>` |
| `[<a>, <b>, <c>, prod]` | skipped | `<a>` | `<b>` | `<c>` | `<c>` |
| `[<a>, <b>, <c>, <d>, prod]` | `<a>` | `<b>` | `<c>` | `<d>` | `<d>` |

The job ids are the slot names; each job's display name, `environment`, concurrency group,
units directory (`<units_root>/<stage>`), ledger record, stop-the-line label and notifications use
the configured stage name. `prod` must be the last stage. A pre-prod stage name must match
`[a-z0-9][a-z0-9_-]*` and must not be `prod`, `prod-pr` or `prod-breakglass`, start with `drift-`
or end in `-plan` (`prod-plan`, `prod-pr-plan` and `<first stage>-plan`, the pull-request plan
environment), because those environments have a fixed role. An unused slot is skipped
exactly as `start-stage` skips it, so the slots after it and `prod-plan` still run. The guard of
every stage job reads the stage order from the same list, so each stage requires the stage before
it to have this SHA live and converged, whatever it is called and whatever slot ran it. Lists of
six or more stages are refused. `iac-pr` plans the first configured stage, and `iac-nightly`
drifts every listed stage.

Why fixed slots and not a matrix over the stages: GitHub does not guarantee the order in which
matrix jobs run, even with `max-parallel: 1`, and `fail-fast` cancels the other legs only on a
best-effort basis. Out of order, the guard would still refuse the later stage (its predecessor has
no record at this SHA), so the run would stay safe but fail at random; and the order would be a
property of the runner, not of the workflow file. With fixed slots the order and the stop on
failure are in `needs:` and `if:`, and the workflow tests check them statically
(`tests/workflows/test_iac_staged.py`): each slot needs the one before it, and a skipped slot
counts as passed only when `resolve` said to skip it, and the slots are identical apart from their
name, `needs:` and `if:`. Raising the limit means adding a slot to the workflow, as a copy of
`stage-1` chained after the last slot and named in `prod-plan`'s `needs:` and `if:`, and raising
`PRE_PROD_SLOTS` in `ayasee.config` (the tests derive the slot list from it). An unused slot is
skipped; its concurrency group carries the run id (`unused-<run id>-stage-<i>`), so it never
waits for or cancels another job.

These layouts are covered by unit tests of the CLI, the guard and the workflow definition; no
two-stage, four-stage or five-stage run has been executed on GitHub yet.

## Flow

| Job | Needs | Runs when | What it does |
|---|---|---|---|
| `resolve` | - | ref is `main` | Refuses with `superseded` when `github.sha` is no longer the tip of `main` and a later commit changes the IaC: a file inside `iac_root` (documentation excepted), the configuration file or anything under `.github/` (R70, `ayasee main-tip --check`: the run of the newer commit promotes the change; a redrive dispatches on the tip). Later commits that leave the IaC alone let the run go on at its own SHA (issue #15); any doubt (an API error, a tip that does not descend from the SHA, 300 changed files or more) refuses, refuses settings that are not supported yet (`ayasee config check-supported`: `gate: risky`, `engine: terraform`, `terragrunt: false`, `extra_rules`, `notify.teams: false`), validates `stages` and `start-stage` (`ayasee config staged`: empty for the first stage, a pre-prod stage name, or `prod-plan`) and reads `.ayasee.yaml` (`stages`, `ledger.url`, `units_root`, `engine`, `identity`). |
| `stage-1` | `resolve` | five stages, and `start-stage` is empty or the first stage | Runs its stage (see [Stages](#stages)): setup, guard, plan, apply (binding mode `same-job`), ledger write. |
| `stage-2`, `stage-3` | the slot before | its stage is in the list and not before `start-stage`; the slot before succeeded, or was skipped (by `start-stage` or as unused) | Same as `stage-1`. |
| `stage-4` | `stage-3` | `start-stage` is not `prod-plan`; `stage-3` succeeded, or was skipped (by `start-stage` or as unused) | Runs the stage just before prod. Same as `stage-1`. |
| `prod-plan` | `stage-1` to `stage-4` | after each slot succeeded, or was skipped by `start-stage` or as unused | setup, guard, plan (`encrypted_plan`), Rego pack and Trivy on the plan JSON, upload the artifact, notify `prod_waiting` when there are changes. Its token holds `contents: read`, `id-token: write` and `issues: read` only: it writes no ledger record, deployment or issue. A change to a root output alone counts as a change (R72). |
| `prod` | `prod-plan` | `has-changes == 'true'` | Gated by the `prod` environment: guard again, read the approver (fails when there is none), download, apply the approved plan, write the ledger, notify. |
| `prod-in-sync` | `prod-plan` | `has-changes == 'false'` | Runs in the `prod-plan` environment (no reviewers). Guard, then write the prod ledger record (and GitHub deployment) with every unit `no_changes` and converged. |

Every stage job has `environment: <name>`, an `ayasee-<repo>-<env>` concurrency group with
`cancel-in-progress: false`, and `github.ref == 'refs/heads/main'` in its `if:`. `prod-plan` runs in
its own `prod-plan` environment, so prod reviewers approve once, on the `prod` job, after the plan
has already been produced and scanned; `prod-in-sync` also runs in `prod-plan` and shares the
`prod` concurrency group with `prod` (both write the prod record).

Every plan and apply that takes the state lock (including the converge re-plan and the replan) passes
`-lock-timeout=<lock-timeout>`, default `10m` (`<n>s` or `<n>m`; set the `lock-timeout` input of the plan and
apply actions to change it). A nightly drift plan (see `drift.md`) holds the same lock, so an overlapping
run waits instead of failing on the blob lease. Plans run with `lock: false` do not use it.

A stage job is skipped only when `start-stage` (or a list too short to use its slot) says so. A
skipped predecessor counts as passed only if it was skipped on purpose; a predecessor skipped because *its* predecessor failed
stops the chain.

The pre-prod stages plan and apply in one job, so they use `binding-mode: same-job` with plan
encryption off (the actions refuse `same-job` for `prod` and the fixed-role environments): the plan file never leaves the job. A failed apply still writes the ledger (the units that
were applied are recorded) and then fails the job, opens or extends the `ayasee:stop-the-line`
issue for the environment, and sends a `stage_failed` notification. The stop-the-line issue blocks
later stages through the guard until it is closed. A prod job that stops before anything is
applied (stale or mismatched plan, or init or decrypt failing) is not an incident: no issue is opened; the job summary and the
`prod_failed` notification (status line "Plan refused: re-plan and approve again") say to re-run
`prod-plan` and approve again.

## Where the actions come from

A reusable workflow's `./actions/...` paths resolve in the *caller's* checkout, not in this
repository, so the workflow cannot use relative action paths when called from another repository.
Checking the repository out at the called workflow's own commit would solve this, but it is not
available everywhere: the `job.workflow_repository` and `job.workflow_sha` properties are
documented as not available on GitHub Enterprise Server ([contexts reference][ctx]), and
`github.job_workflow_sha` has been reported to be empty inside reusable workflows
([actions/runner#2417][issue]). So the workflow references its actions by repository path, with a
placeholder that the release process replaces:

```yaml
uses: OWNER/ayasee/actions/plan@0000000000000000000000000000000000000000 # AYASEE_REF
```

- **Release (option i).** `scripts/set-ayasee-ref.sh <owner/repo> <source-sha>` rewrites every
  `# AYASEE_REF` line to `<owner/repo>/actions/<x>@<source-sha>`. `<source-sha>` is the commit
  that holds the released code; the script refuses unless `git diff --quiet <source-sha> HEAD --
  actions/ policy/ src/` shows no difference, so the pinned actions are exactly the released code
  and the release commit differs only in the workflow file. Callers pin the release commit by SHA.
- **In-repo e2e (option ii).** `scripts/set-ayasee-ref.sh --local` rewrites the lines to
  `./actions/<x>`, for runs inside this repository where `./actions` is the checkout.

Until one of these has run, the committed workflow is a template and cannot be called. A test
checks that all placeholders share one ref.

[ctx]: https://docs.github.com/en/actions/reference/workflows-and-actions/contexts
[issue]: https://github.com/actions/runner/issues/2417

## Environments and their controls

The staged run uses four GitHub environments with the default stages: `dev`, `uat`, `prod-plan` and
`prod` (three with a two-stage list: `<pre-prod>`, `prod-plan`, `prod`). The whole set the workflows
expect with the default stages is nine (add `dev-plan`, named `<first stage>-plan`, for pull requests, `prod-breakglass` for emergencies, and
`drift-dev`, `drift-uat` and `drift-prod` for the nightly check); the
[`github-environments`](../../modules/github-environments/README.md) module creates all of them, and
[architecture](architecture.md#environments-and-identities) lists what each may do. On every staged
environment:

- **Deployment branches: `main` only.** This is the real control. The `if: github.ref ==
  'refs/heads/main'` in each job is only defence in depth.
- **Admins cannot bypass** protection rules (D2).

On `prod`: **required reviewers** and **`prevent_self_review: true`** (D2), so the person who
merged cannot approve the production apply. `dev`, `uat` and `prod-plan` have no reviewers. The
`prod` job reads the approvers from the approvals API and refuses to apply when there are none.

## Environment variables (per environment)

With `identity: oidc` (the default) there are no cloud secrets. Set these as **variables** on each
GitHub environment, each backed by its own Entra user-assigned managed identity with a federated
credential for `environment:<name>`:

| Variable | Purpose |
|---|---|
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | `azure/login` (OIDC), and `ARM_CLIENT_ID`, `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID` for the provider |
| `AYASEE_LEDGER_URL` | Ledger store URL. Falls back to `ledger.url` in `.ayasee.yaml`; the run fails if neither is set. An `s3://` URL with `endpoint=` must be set here, never in the file. |
| `AYASEE_KEY_VAULT_KEY_ID` | `plan_encryption.key_provider: azure_vault` (the default) only. Azure Key Vault key URL (`https://<vault>.vault.azure.net/keys/<name>`, no key version) protecting plan encryption. Set the same key on `prod-plan`, `prod` and `prod-breakglass`. `prod-plan` needs `wrapKey` and `unwrapKey` (it encrypts its plan, then decrypts it for `tofu show -json`), `prod` needs `unwrapKey`, `prod-breakglass` both. Full table: [azure-stage-identity](../../modules/azure-stage-identity/README.md#role-assignments-not-created-here). |

The `prod-plan` identity reads and plans prod; it must not be able to apply. It is also granted
write on the prod ledger record, because `prod-in-sync` records an unchanged prod from that
environment.

With `identity: oidc` the workflow runs `azure/login` and sets `ARM_USE_OIDC=true`, `ARM_CLIENT_ID`,
`ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID` and `DATABRICKS_AUTH_TYPE=github-oidc-azure`. With
`identity: secrets` it skips `azure/login` and sets those variables to empty strings, for providers
that cannot federate with GitHub OIDC (see [the reference](../reference/ayasee-yaml.md#keys)). Two optional
secrets are declared explicitly under `on.workflow_call.secrets`, never inherited: `teams-webhook`
(without it, notifications are skipped) and `ledger-token`, a fine-grained token with contents
write on the repository, needed only for a `git-branch://` ledger store. A third,
`AYASEE_PLAN_PASSPHRASE`, is only for `plan_encryption.key_provider: pbkdf2` and must be an
environment secret of `prod-plan` and `prod` (see [key providers](plan-binding.md#key-providers)). Jobs keep `contents: read`;
when `ledger-token` is set, only the guard and ledger steps receive it, as step-level environment
(`GIT_CONFIG_*` with an inline credential helper that reads the token from an environment
variable; no file, no global git configuration), plus the trunk-guard step of the `resolve` job,
which uses it for read-only API calls only (next section). For an `s3://` ledger, the same
guard and ledger steps, and no other, receive the dedicated ledger key
(`AYASEE_LEDGER_AWS_ACCESS_KEY_ID` and `AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY`, environment secrets)
as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, never the state key of the plan and apply
steps (see [the ledger key](ledger.md#the-ledger-key)).

### The ledger token and `main`

A token with contents write can push to any branch, `main` included. When `ledger-token` is passed,
the `resolve` job of `iac-staged` and `iac-nightly` runs `ayasee trunk-guard` with that token (one
step, read-only calls). It passes when an active ruleset with a `pull_request` rule applies to
`main` and the token has no bypass for it, which means the token cannot push to `main` directly.
It reads `GET /repos/{owner}/{repo}/rules/branches/main` and
`GET /repos/{owner}/{repo}/rulesets/{id}?includes_parents=true`, which need only the Metadata read
permission that every fine-grained token carries; `current_user_can_bypass` then describes the
ledger token itself. Without a pull-request rule it reads `protected` from
`GET /repos/{owner}/{repo}/branches/main` (Contents read) to tell an unprotected `main` from
classic branch protection, whose bypass settings need the Administration permission and so cannot
be verified. The workflow's `github.token` is not used: it can read the rules but not who may
bypass them, and no job permission is added.

What a pass does not cover: with zero required approvals, or without "require approval of the most
recent push", the token can still push to the head branch of an open pull request and merge it
through the API. Zero approvals is accepted because the solo-maintainer path needs it; require an
approval and the most-recent-push approval where you have a second reviewer.

Anything else is a `::warning::` and a line of the run summary, not a refusal: `main` unprotected,
classic branch protection (not verifiable), a ruleset that is not active, a bypass for the token
(`always`, `exempt`, or `pull_requests_only` when merging a pull request), an unknown value or an
API error ("cannot tell"). Not verified is never reported as protected. This warning is a
deliberate exception to the fail-closed rule of [CONTRIBUTING](../../CONTRIBUTING.md#ground-rules):
the check reads repository settings, not an input of the run, and a refusal would stop repositories
protected by classic branch protection on every push. `iac-breakglass` does not run the check: it
is the incident path, and the staged run of every push already reports it.

## identity: secrets

Some providers (Cloudflare, most VPS hosts, many S3-compatible stores) cannot federate with GitHub
OIDC. `identity: secrets` in `.ayasee.yaml` is the opt-in for them, and a documented deviation from
the "no stored cloud secrets" default (D7):

- No `azure/login`; `ARM_*` and `DATABRICKS_AUTH_TYPE` are empty.
- The plan and apply steps, and no other step, receive three provider tokens as environment
  variables: `CLOUDFLARE_API_TOKEN`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (the last two
  for an S3-compatible state backend). They are declared, optional, under `on.workflow_call.secrets`
  of every reusable workflow; with `identity: oidc` the steps get empty strings instead.
- Create them as **environment secrets**, one set per environment (`dev`, `uat`, `prod-plan`,
  `prod`, `prod-breakglass`, `<first stage>-plan`, `drift-<stage>`), each token scoped to what that
  environment may do, and never at repository or organisation level. The caller passes them by
  name, never with `secrets: inherit`:

  ```yaml
      secrets:
        CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
        AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
        AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
  ```

  GitHub's documentation says a job that runs in an environment receives that environment's secret
  when the caller passes a secret of the same name; this has not been verified by a run yet.
- Other providers (OVH, Hetzner, Scaleway, a second Cloudflare account) use the
  [provider environment](#provider-environment) below, without a change in ayasee.
- The first job of each reusable workflow runs `ayasee config labels` and turns
  `DEVIATION: identity: secrets ...` (and `NON-COMPLIANT: solo_mode ...`) into a warning and a line
  of the run summary. The cockpit does not show the label yet.

What it costs: a stored token does not expire after the job like an OIDC token, so a leaked token
stays usable until it is rotated. And a stage is isolated only as far as its tokens' scopes are: a
provider that scopes tokens to a zone or an account, not to objects, lets a pre-prod token that
applies without approval change prod objects of the same zone, and the prod gate then protects the
workflow, not those objects. The `DEVIATION` label says so; the cockpit tag stays
`secrets identity · deviation`. The `<first stage>-plan` tokens (`dev-plan`, `test-plan`) are the
most exposed, because `iac-pr` plans pull-request code with them (same-repository pull requests
only; forks get no secrets): give that environment read-only tokens. The prod tokens live only in
`prod` and `prod-breakglass`, behind their
reviewers; `prod-plan` should hold read-only ones.

These tokens never reach the plan key. With `plan_encryption.key_provider: aws_kms` the plan key is
reached with its own key pair, `AYASEE_KMS_ACCESS_KEY_ID` and `AYASEE_KMS_SECRET_ACCESS_KEY`,
environment secrets of `prod-plan`, `prod` and `prod-breakglass` only (see
[key providers](plan-binding.md#key-providers)).

### Provider environment

`workflow_call` secrets are declared statically and the callers never use `secrets: inherit`, so
the reusable workflows cannot name a secret for every provider. One more optional secret,
`AYASEE_PROVIDER_ENV`, carries the other provider variables as one JSON object of name to string
value, and `.ayasee.yaml` declares the names it may hold:

```yaml
identity: secrets
provider_env: [HCLOUD_TOKEN, OVH_APPLICATION_KEY, OVH_APPLICATION_SECRET, OVH_CONSUMER_KEY]
```

```bash
# one secret per environment, scoped like the named tokens; the JSON never goes on a command line
gh secret set AYASEE_PROVIDER_ENV --env test < provider-env.test.json
```

```yaml
    secrets:
      AYASEE_PROVIDER_ENV: ${{ secrets.AYASEE_PROVIDER_ENV }}
```

- **Same scope as the named tokens.** Only the plan and apply steps receive it, and only with
  `identity: secrets`. Create it as an environment secret of each environment that needs it, never
  at repository or organisation level. An environment without it gets none of the variables.
- **Declared names only.** The plan and apply scripts run `ayasee provider-env` first. It refuses
  the run when the secret holds a key that `provider_env` does not declare (it counts such keys,
  it never prints them), when a value is not a JSON string, when a key repeats or when the JSON
  is malformed (the error gives a line and column, never the text). Whoever can write the secret
  cannot add a variable that the configuration, reviewed in a pull request, does not name.
- **Reserved names are refused**, both in `provider_env` (the configuration does not load) and at
  run time: names that change what the job runs, where its traffic goes or what it logs. That is
  `PATH`, `HOME`, `ENV`, `IFS`, `TMPDIR`; the loader and interpreter variables (`LD_*`, `DYLD_*`,
  `PYTHON*`, `NODE_*`, `PERL5OPT`, `RUBYOPT`, `JAVA_TOOL_OPTIONS`, `GODEBUG`, `OPENSSL_CONF`,
  ...); bash's own variables, because the scripts export into their own shell (`BASH*`, `UID`,
  `EUID`, `PPID`, `POSIXLY_CORRECT`, `FUNCNEST`, `TMOUT`, `EXECIGNORE`, `OPTIND`, `SECONDS`,
  `RANDOM`, `HIST*`, `PS0` to `PS4`, `PROMPT_*`, `COMP_*`, `READLINE_*`, ...); the runner's
  (`GITHUB_*`, `RUNNER_*`, `ACTIONS_*`, `INPUT_*`, `CI`); the engine's and Terragrunt's (`TF_*`,
  `TG_*`, `TERRAGRUNT_*`, `TERRAFORM_*`: `TF_CLI_ARGS`, `TF_LOG`, `TF_ENCRYPTION`,
  `TERRAFORM_CONFIG`, ...); ayasee's own (`SG_*`, `AYASEE_*`); git and SSH (`GIT_*`, `SSH_*`);
  TLS trust, key logs and proxies (`SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `SSLKEYLOGFILE`,
  `HTTPS_PROXY`, `NO_PROXY`, ...); libc and the resolver (`GLIBC_TUNABLES`, `HOSTALIASES`,
  `RES_OPTIONS`, `LOCALDOMAIN`); stored passwords and other shells' start-up files (`NETRC`,
  `CURL_HOME`, `KSH_ENV`, `ZDOTDIR`); `XDG_*`, `LANG` and `LC_*`; and the whole `AWS_*` and `CLOUDFLARE_*` families, which belong to
  the named tokens and the state backend (`AWS_ENDPOINT_URL*`, `AWS_PROFILE`, `AWS_CONFIG_FILE`
  or `CLOUDFLARE_BASE_URL` would send those credentials elsewhere or run a command). A second
  Cloudflare account goes through a `TF_VAR_<variable>` that the unit passes to its provider
  block. A name is `[A-Z][A-Z0-9_]*` or `TF_VAR_` and a variable name. The full list is
  `RESERVED` and `RESERVED_PREFIXES` in [`src/ayasee/providerenv.py`](../../src/ayasee/providerenv.py).
- **Values of at least 16 characters.** A mask hides every occurrence of the string in the job
  log, and the runner also drops any job output that contains a masked string ("Skip output ...
  may contain secret"). A value such as `true` would empty the `has-changes` output of
  `prod-plan`, and prod would be skipped without an error. So a value shorter than 16 characters
  (after trimming spaces) is refused. Most provider credentials are longer (an
  `OVH_APPLICATION_KEY` is exactly 16), but a short real secret, such as a short password passed
  as a `TF_VAR_*`, is refused too: lengthen it, or keep it out of this secret. Non-secret
  identifiers (an endpoint name, an account or zone ID) belong in the unit inputs.
- **Masked before use.** Each value is registered with `::add-mask::` before anything else the
  step prints. The runner matches masks against one log line at a time, so a mask that contains a
  newline never matches: a multi-line value (a PEM key, written with `\n` in the JSON) is hidden
  by its lines alone, each line of 8 characters or more registered on its own (random base64 of
  that length does not collide with ayasee's outputs). One line may be shorter, the tail of a
  base64 block; it is not masked and can appear in the log. A value with more than one such short
  line is refused: write it on one line (minified JSON, for example). Masks cover the job log
  only: a value that reaches a resource attribute or a non-sensitive variable lands in the plan
  JSON and the state like any other value.
- **Never outside the step.** The variables are exported in the plan or apply script's own
  process and the engine it starts: never through `$GITHUB_ENV`, never on a command line, never to
  a later step. The pairs pass from `ayasee provider-env` to the script through a mode-600 file
  under `$RUNNER_TEMP`, deleted before anything is exported. The script unsets the raw JSON in its
  shell, so the engine's environment does not carry it; the script's own process environment
  (`/proc/<pid>/environ`) still does, as it does the named tokens.

**What a reviewer of `provider_env` checks.** A declared name is a decision about where
credentials go, so protect `.ayasee.yaml` with `CODEOWNERS` and review a change to the list like a
change to a provider block:

- A variable that holds a URL, an endpoint or a file path (`HCLOUD_ENDPOINT`, `SCW_API_URL`,
  `OVH_ENDPOINT`, a `*_CONFIG_FILE`) decides where the provider sends its credentials, or what file
  it reads them from. Whoever can write the secret then chooses that destination. Prefer to set it
  as a unit input in reviewed code.
- A `TF_VAR_<variable>` overrides the unit's `inputs` of the same name: Terragrunt passes `inputs`
  as `TF_VAR_*` variables, and its reference ("Variable Precedence" under `inputs`) states that an
  explicitly set `TF_VAR_*` environment variable takes precedence over `inputs`. Declare a
  `TF_VAR_` name only for a variable that no `terragrunt.hcl` sets, and mark the variable
  `sensitive = true` in the module, or its value appears in the plan JSON and the state.

The trust model is that of the named tokens: code planned in a pull request can read whatever the
`dev-plan` environment holds, `provider_env` included, so `dev-plan` gets read-only credentials.
The names `iac-pr` uses come from the pull request's own `.ayasee.yaml`, which widens nothing:
every key in the secret has to be declared or the run fails.

## Units that serve every stage

Every stage plans and applies the units under `<units_root>/<stage>/`. A unit does not have to exist
in every stage: a unit present under `prod` only is planned by `prod-plan`, shown in the gate
summary and applied by `prod` behind the approval, and `dev` and `uat` never see it. The ledger
records it with the prod run.

Some resources exist once and carry the traffic of every stage: one tunnel whose ingress lists the
test and the prod hostnames, one access application, one DNS zone. They cannot be split per stage,
and putting them under the first stage would apply prod traffic changes without approval. Put them
under `prod` and list them in `shared_units` in `.ayasee.yaml`:

```yaml
shared_units: [edge__tunnel, edge__access]   # stages/prod/edge/tunnel, stages/prod/edge/access
```

Then:

- `resolve` and the PR `checks` job run `ayasee config check-shared`: a listed unit missing under
  `prod`, or also present under another stage, fails the run;
- the gate summary opens with "Shared by every stage" and marks each of their changes **shared**,
  and the change set lists them in `shared_units`, because the stage name alone does not tell the
  reviewer the blast radius;
- a pull request that changes one of them gets a second plan, against prod and read-only, in the
  `prod-pr-plan` environment ([PR checks](pr-checks.md#shared-units)).

The trade-off is explicit: a change to a shared unit, even one that only concerns a test hostname,
reaches production only through the prod gate, and no earlier stage soaks it.

## The plan artifact

`prod-plan` uploads one artifact, `ayasee-prod-plan`, kept 7 days. It holds only the encrypted
plan files (`plans/<unit>.tfplan`) and `manifest.json` (plan hashes, engine version, provider lock
hashes). The plan JSON stays in the job: the Rego pack (conftest, with a data file that sets the
stage to `prod`) and the Trivy `config` scan (HIGH and CRITICAL, exit code 1) both run on it before
the upload, and the Trivy report is written to a file that is never uploaded; only rule ids and
severities are printed. A finding stops the run before anything is uploaded.

`prod-plan` outputs `has-changes`, `critical`, `high` and `units` (a JSON array of unit names).
The `prod` job downloads the artifact and applies those exact files after checking their hash,
the engine version and the provider lock hash. A stale or mismatched plan stops the run: re-plan
and approve again.

## Approval and the ledger

`prod` waits on the `prod` environment's required reviewers. Right after the guard, before the
artifact is downloaded or anything is applied, it asks the approvals API who approved `prod` on
this run (`ayasee approvals list --env prod`): every distinct login with an `approved` entry for
`prod`, sorted and comma-joined (`alice,bob`). It fails closed if there is none. Rejected entries
are never listed. The API has no attempt field, so approvals from earlier attempts of the same run
may be included: the recorded approver string is a superset of the people who approved this
attempt. It is stored in the ledger with `--approver`. The ledger in the backend is the source of
truth for what is live; the GitHub deployment is a projection written with `--mirror-deployments`.

## The empty-plan path

When every unit plans clean, `has-changes` is `false`: `prod` is skipped and `prod-in-sync` runs
instead. It runs in the `prod-plan` environment, so it never waits for prod reviewers. It still
passes the guard, then writes the prod ledger record (and the deployment projection) at this SHA
with every unit `no_changes` and converged, built from the `units` output (an empty list is
refused). Drift checks therefore never keep planning an older SHA. A failure notifies
`prod_failed`.

## Redrive

To resume from the middle of a promotion, call the workflow with `start-stage: prod-plan` (for
example after fixing a prod-plan failure with `uat` already live at the SHA). Earlier jobs are
skipped but the guard still runs in `prod-plan`, so the run is refused unless the preceding stage
has this SHA live in the ledger. Re-running failed jobs of an existing run reuses its artifact for
7 days.

## The supersession guard

Before each stage, `ayasee guard` checks the ledger: the preceding stage must have this SHA
live and converged (a different converged SHA there means `superseded`), and no open
`ayasee:stop-the-line` issue may exist for the stages up to prod. The `prod` job runs the guard
again after the approval wait, at apply time: a run that was approved while newer commits reached
uat is rejected as `superseded` and is not applied.

## Dependencies between units (R52)

A unit that reads another unit's outputs (a Terragrunt `dependency` block) is planned with the
outputs its upstream has *before* the upstream is applied. How the run handles that depends on the
binding mode:

- `same-job` (dev, uat) and `breakglass`: the plan made by the plan action is a preview. The apply
  action works one unit at a time in dependency order: it plans the unit again, applies that fresh
  plan, then runs the converge check, so every dependent is planned with the upstream's new outputs. In break-glass the policy pack
  checked the preview, so the fresh plan of each unit must propose the same `(address, actions)`
  set; otherwise the run stops before that unit's apply ("plan changed since the policy check; re-run
  break-glass"). Dev and uat have no policy gate and apply the fresh plan.
- `encrypted_plan` and `replan` (prod): the approved plan cannot be remade. `prod-plan` therefore
  refuses with `cascading change: <dependent> depends on <upstream>, which also changes; land
  <upstream> first` when a unit with changes reads, directly or through other units, the outputs of
  another unit with changes. Land the upstream unit in its own merge. Units that only list another
  unit under `dependencies` (ordering, no outputs) are not affected.
  `allow_cascade: true` in `.ayasee.yaml` turns the refusal off. It is unsafe: the converge check
  after each apply still catches a dependent that no longer matches the code, but only after the
  upstream was applied.

## A rejected prod gate

When a reviewer rejects the `prod` environment the job ends without running any step, so it cannot
notify. The `prod-result` job (runs when `prod` failed and there were changes) reads the run's
approvals with `ayasee approvals rejected --env prod --job prod` and posts `prod_rejected` when the gate was
rejected. The approvals API has no attempt field, so a rejection from an earlier attempt is only
counted when the `prod` job of the latest attempt ran no step. An apply failure is not announced there: the `prod` job already posts `prod_failed`.
