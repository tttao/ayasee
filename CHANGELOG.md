# Changelog

All notable changes to ayasee are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/). The release workflow copies the section of the
released version into the GitHub release notes.

## [Unreleased]

### Fixed

- **`secret_commands` on GitHub Free printed the narrowest scope** (#47): with
  `gate_mechanism: workflow` on a private Free repository every secret is one repository secret,
  but `github-environments` kept the scope of whichever environment sorted last (`test-plan`, a
  read-only one), so the credentials it described could not apply. It now prints, for each name,
  every scope listed for it, which the one value must hold, and says that the pull-request plan
  reads it too; a name in both `repository_secret_scopes` and `secret_scopes` is refused there.
  The Free warning of `ayasee doctor`, D15, the adoption guide and the `iac-pr` caller comment say
  what that means: the pre-prod jobs and the pull-request plan hold the credentials that edit prod
  and write its state.
- **`ayasee init` with an existing `.ayasee.yaml`** (#48): a valid, customised configuration used
  to block the scaffold, and `--force` reset it to the template (losing `reviewers.prod` and
  `solo_mode`). Init now keeps a configuration that loads (one that only lacks the
  `reviewers.prod` placeholder's replacement counts), whatever `--force` says, and takes
  `stages`, `iac_root`, `environment_prefix` and `gate_mechanism` from it, so the callers and the
  bootstrap it writes match it. A flag that contradicts the file, a gate the plan detection would
  not choose (pass `--gate-mechanism` with the file's gate) and a `units_root` init does not
  scaffold are refused with nothing written; a configuration that does not load is refused
  untouched. The new `--replace-config` overwrites it with the template.
- **Every caller of the reusable workflows failed in "Set up job"** (#49) since
  `v0.1.0-rc.3`: the repository tracked 183 symlinks in `.claude/skills/` that pointed into a git
  submodule, and the tarball a runner downloads for an action holds no submodule, so the runner
  could not stage ayasee. The symlinks and the submodule are no longer tracked (`.claude/skills/`
  and `.claude/vendor/` are ignored). A new `scripts/check-archive.sh` refuses a commit whose
  archive holds a submodule or a symlink that does not resolve inside it; it runs in CI (`action
  archive`) and in the first job of the release workflow.

## [0.1.0] - unreleased

First release (pre-alpha), published as release candidates: `v0.1.0-rc.2` (the run of
`v0.1.0-rc.1` failed before publishing anything), `v0.1.0-rc.3`, then `v0.1.0-rc.4`; the changes
of each candidate are listed newest first. The date is set when the final version is tagged, see [releasing](docs/guides/releasing.md).

### Since v0.1.0-rc.3

#### Changed

- **Breaking for private repositories in `github-environments`**: the module reads the
  repository's visibility, and a private repository must set the new `private_repository_plan`
  (`free`, `pro`, `team` or `enterprise`); the plan refuses every environment whose reviewers or
  `main`-only policy that plan cannot hold, which in practice means `enterprise`. Public and
  internal repositories need nothing. A postcondition fails when GitHub did not keep the reviewers
  of a gated environment. Both example bootstraps pass `private_repository_plan` through.
- **`iac-staged`: a new `prod-gate` job** (`actions: read` only, no environment) runs
  `ayasee require-reviewers` on the `prod` environment (prefixed name) beside the pre-prod stages,
  and `prod-plan` needs it: no plan is handed to a `prod` that would not wait for an approval.
  `solo_mode` does not lift it (the reviewers stay required). **`iac-breakglass`**: the `validate`
  job now holds `actions: read` and runs the same check on `prod-breakglass`.

#### Added

- **Break-glass with `gate_mechanism: workflow`** (#40, decisions
  [D3 and D15](docs/governance/decisions.md#break-glass-with-the-workflow-gate)): the emergency
  path no longer refuses to run in that mode. It is a request and an approval, with the guarantees
  of the prod gate of that mode.
  - `.ayasee.yaml`: `reviewers.breakglass`, the GitHub logins that may approve a break-glass
    request (only with `workflow`; `iac-breakglass` refuses without it). It may share people with
    `reviewers.prod` but not be the same list, unless `solo_mode`. A change of it is a change of
    the gate settings, which always needs a prod approval.
  - **`iac-breakglass`**, in this mode (read from `main`): a `request` job plans the whole prod tree
    in `prod-plan` (the plan identity), runs the policy pack and the rollback rule, and keeps the
    encrypted plan, its manifest and `request.json` (incident, SHA, allow-rollback, counts) as
    `ayasee-breakglass-plan-<sha>`; a `request-issue` job (no environment, no secret) opens an
    `ayasee:breakglass-approval` issue with the digest. Nothing is applied. The job of the
    environment gate is unchanged.
  - **`iac-approve`** gains an optional `incident` input: with it, the run approves a break-glass
    request. `verify` checks the reviewer in `reviewers.breakglass` of `main`, the newest plan of a
    successful `iac-breakglass` dispatch on `main`, its digest and `request.json`, and that the
    approver is not the requester (the request run's actor or triggering actor) and did not write
    or merge the change, unless `solo_mode`. A new `breakglass` job applies in `prod-breakglass`:
    no guard (as before), the rollback rule again, the ledger checks of the break-glass role
    (listed at the last approved apply too; no commit since; the plan is refused once prod changed
    after it was made, so an old approval cannot be replayed), the apply of exactly that plan, the
    ledger record with the incident, the approver and the requester, and the post-approval issue.
  - The ledger record gains an optional `requester` (`ayasee ledger write --requester`, `requester`
    input of the ledger action); records without it keep their shape.
  - New `ayasee approve request` and `ayasee approve breakglass-issue`; `approve authorize
    --incident`, `approve baseline --role breakglass --plan-started-at`, `approve close-issue
    --breakglass`, `issue post-approval --counts-json --requester`.
  - `ayasee doctor`: every login of `reviewers.breakglass` needs write access; an `iac-breakglass`
    caller without `reviewers.breakglass` is a warning (no emergency path), and one whose
    `iac-approve` caller does not pass `incident` a failure; the
    environments, secrets and variables of the request (`prod-plan`) and of the break-glass apply
    (`prod-breakglass`) are checked. `github-environments` keeps `prod-breakglass` main-only and
    bare of reviewers, as before. The example `iac-approve` callers (written by `ayasee init`)
    pass `incident`, and the scaffolded `.ayasee.yaml` of the workflow gate shows
    `reviewers.breakglass` as a comment.
- **The pull-request plan of shared units with `gate_mechanism: workflow`** (#40, adopter
  blocker 3, [D15](docs/governance/decisions.md#d15-the-workflow-gate)): a new reusable workflow,
  **`iac-pr-shared`**, that a prod reviewer starts on `main` with a pull request's number and
  head SHA. Its `verify` job (no environment, no secret) checks the dispatch (on
  `refs/heads/main`, not re-run by someone else), the reviewer (in `reviewers.prod` of `main`'s
  configuration, write access), the pull request (open, same repository, into `main`, head
  exactly that SHA), independence (not its author nor an author or committer of its commits,
  unless `solo_mode`) and `prod-pr-plan` (deploys from `main` only). Its `plan` job checks out
  exactly that SHA in `prod-pr-plan` and plans the changed shared units read-only, like
  `plan-shared`; its `comment` job (no pull-request code) posts the `pr-plan-shared` comment,
  naming the commit and the reviewer, and says when the pull request has moved on. Not
  available on a private repository on GitHub Free, where `prod-pr-plan` could be neither
  restricted to `main` nor given credentials apart from the prod apply's: the run refuses it,
  the bootstrap refuses `shared_units_pr_plan` and `ayasee doctor` fails on the caller. New
  `ayasee pr-shared authorize|comment`.
  - `iac-pr` in this mode: `shared-changes` now works out the changed shared units and, when
    there are some, sets the new `pending` output instead of planning them; `comment-shared`
    then says which pull request and head SHA a prod reviewer authorises.
  - `github-environments`: with `gate_mechanism = "workflow"`, `shared_units_pr_plan` creates
    `prod-pr-plan` without reviewers and restricted to `main` (refused with
    `private_repository_plan = "free"`).
  - `ayasee doctor` knows the `iac-pr-shared` caller: with the workflow gate it checks that
    `prod-pr-plan` exists, deploys from `main` only and holds the secrets the caller passes, and
    warns when `shared_units` are set but nothing calls `iac-pr-shared`. `ayasee config
    environments` lists `prod-pr-plan` whenever `shared_units` are set.
- **`gate_mechanism: workflow`, a prod gate that ayasee enforces itself** (#40, decision
  [D15](docs/governance/decisions.md#d15-the-workflow-gate)): for a private repository below
  GitHub Enterprise, which has no required reviewers on environments. Opt-in, labelled
  `DEVIATION: gate enforced by ayasee, not by GitHub environment protection` on every run and in
  the cockpit (`workflow gate`). The default, `environment`, does not change.
  - `.ayasee.yaml`: `gate_mechanism` (`environment` or `workflow`) and `reviewers.prod`, the GitHub
    logins that may approve; required with `workflow`, refused with `environment`.
  - **`iac-staged`** stops after `prod-plan` in this mode: a new `prod-approval` job (no
    environment, `contents: read` and `issues: write`) opens or rewrites one issue labelled
    `ayasee:prod-approval` with the commit SHA, the SHA-256 of the plan manifest and the change
    counts; the `prod` job is skipped. `prod-plan` gains the `changes` and `manifest-digest`
    outputs.
  - **`iac-approve`**, a new reusable workflow that a reviewer starts on `main` with `sha` and
    `manifest-digest`. Its `verify` job (no environment, no secret) checks the dispatch (on
    `refs/heads/main`, not re-run by someone else), the reviewer (in `reviewers.prod` of `main`'s
    configuration, write access), the commit (on `main`), the plan (the newest unexpired
    `ayasee-prod-plan` of that commit, from a successful `iac-staged` run on `main`, whose manifest
    has that digest), independence (not who pushed, wrote or merged it, unless `solo_mode`) and
    the prod environment (main-only, or a plan without branch policies, said as a deviation). Its
    `apply` job runs the guard, then checks against the ledger (not already live; the reviewer was
    already listed at the last approved prod apply, never at a commit that went live in sync,
    with a `DEVIATION: bootstrap trust` warning before the first approval; no commit since is
    theirs), applies exactly the
    approved plan and records the approver. New `ayasee approve authorize|digest|baseline|gate-change|issue|close-issue`.
  - A change of `gate_mechanism`, `reviewers.prod` or `solo_mode`, with the workflow gate on
    either side, is never promoted in sync: `prod-plan` gains a `gate-changed` output (`ayasee
    approve gate-change`), and such a commit goes to `prod-approval` (or to `prod` when it sets
    the environment gate) even with an empty plan.
  - `ayasee doctor` in this mode: Pro or Team is fine, Free warns what the gate does not protect,
    every reviewer must have write access, an `iac-approve` caller must exist, and on Free the
    secrets and variables are checked at repository level. With the environment gate, a plan
    without reviewers now points to `gate_mechanism: workflow`.
  - `github-environments`: new `gate_mechanism`. With `workflow`, no environment carries
    reviewers (`prod_reviewers` and `breakglass_reviewers` are left unset), and
    `private_repository_plan = "free"` is accepted: the environments are created bare (no branch policy, no marker variable) and `secret_commands` lists repository secrets.
  - `iac-pr` never plans shared units in this mode (it reads the mode of the base commit too, so
    a pull request cannot switch its own plan back on; see `iac-pr-shared` above).
- **`ayasee init` chooses the prod gate from the GitHub plan** (#40): a private repository it
  reads as GitHub Free, Pro or Team gets `gate_mechanism: workflow`, the `iac-approve` caller
  (pinned like the other callers, with exactly the permissions of the reusable workflow's jobs
  and the secrets of the `iac-staged` caller) and the plan in the bootstrap; a public repository
  or GitHub Enterprise keeps `environment` (Enterprise is stated as `private_repository_plan`);
  a plan it cannot read keeps `environment` and says which flags to pass, never the weaker gate
  ([adopt: the gate follows the plan](docs/guides/adopt.md#1-scaffold-ayasee-init)).
  - New `--gate-mechanism environment|workflow` and `--plan free|pro|team|enterprise` override the
    detection, with a warning when they go against it; `--upgrade` refuses both and moves the pin
    of `iac-approve.yml` like the others.
  - With `workflow`, `reviewers.prod` is the placeholder `REPLACE_WITH_GITHUB_LOGIN`, which
    `.ayasee.yaml` refuses with its own message (so no run and no `ayasee doctor` passes) until it
    is replaced by GitHub logins.
  - The example bootstraps take `gate_mechanism` (default `environment`) and pass it to
    `github-environments`; `prod_reviewers` and `breakglass_reviewers` default to unset, which the
    module still refuses with the environment gate. The Cloudflare bootstrap on private GitHub Free
    sets `AYASEE_LEDGER_URL` as a repository variable. Both examples ship the
    `iac-approve.yml` caller, for that mode only.
  - `init` without a repository to read now says that it kept the default gate, and how to choose.
- **What the GitHub plan must allow** (#39): the default gate rests on required reviewers on
  environments (GitHub Enterprise only in a private repository), deployment branch policies and
  rulesets (GitHub Pro, Team or Enterprise in a private repository). A private repository on
  another plan is not supported by the default gate today, and ayasee now says so at every step
  instead of running ungated ([adopt: what the plan must allow](docs/guides/adopt.md#what-the-github-plan-must-allow);
  a plan-independent gate is tracked in #40). `ayasee.plansupport.detect` reads the repository's
  visibility, the owner's plan when the token may see it, the 403 "Upgrade to GitHub Pro" of the
  rulesets API and the reviewers already set, and answers `supported`, `unsupported` (with the
  missing features) or `unknown`, never `supported` without evidence.
  - `ayasee doctor` checks the plan **first**: a plan that lacks the features is one failure that
    names them and the guarantees that fall (the prod gate, `prevent_self_review` and the
    break-glass reviewers; the `main`-only environments; the guard of the ledger token), and the
    GitHub settings are then not listed one by one; a plan it cannot confirm fails too. The raw
    `HTTP 403: Upgrade to GitHub Pro` on the rules of `main` is reported in ayasee's words.
  - `ayasee init` runs the same check before it scaffolds, when it can reach the repository
    (new `--repo`, else the `origin` remote), and warns; the scaffold is still written.
  - `ayasee require-reviewers --repo --environment`: refuses a gated environment that has no
    required reviewers, does not exist, cannot be read, or whose plan ignores them.

### Since v0.1.0-rc.2

#### Changed

- **Upgrading an existing bootstrap (#31):** its environments have no `AYASEE_MANAGED` marker, so the
  new check would take them for foreign. Apply once with `adopt_existing_environments = true`
  (nothing else changes: it writes the marker), then drop the flag. `iac-breakglass` now checks out
  and reads `environment_prefix` from the ref it runs from (main) in its `validate` job, before
  the job that names its environment starts.
- **Breaking for estates whose first stage is not `dev`** (#11): the pull-request plan environment
  is named after the first stage, `<first stage>-plan`, matching `prod-plan`: `stages: [test, prod]`
  plans pull requests in `test-plan`, no longer in `dev-plan`. With the default stages, or any list
  that starts with `dev`, nothing changes. A new `resolve` job of `iac-pr` names the environment
  with the new `ayasee config pr-plan-environment`, and the plan runs in it, in a job now called
  `plan-first-stage`. `plan-dev` stays the check to require: it no longer plans but fails unless
  `resolve` accepted the configuration and `plan-first-stage` passed, so a refused configuration
  turns it red instead of skipping it, and the comment says so. The comment is headed
  "Plan of `<stage>`" instead of "Dev plan". The
  `github-environments` module creates `<first stage>-plan` instead of `dev-plan` (new output
  `pr_plan_environment`) and refuses a first stage named `prod`. To move an estate whose first stage
  is not `dev`: apply the module (it destroys `dev-plan` and creates `<first stage>-plan`, so the
  environment secrets of `dev-plan` go with it), re-create the provider tokens of
  `identity: secrets` and the `AZURE_*` variables in the new environment, and with Azure OIDC
  re-create the federated credential for the subject `environment:<first stage>-plan`
  (`azure-stage-identity` with `environment = "<first stage>-plan"`). There is no setting that
  keeps the old name. If `dev-plan` or its federated credential was created outside the module,
  delete both: a leftover `dev-plan` accepts every branch and still holds a read identity.
- `ayasee init` scaffolds the callers as `iac-pr.yml`, `iac-staged.yml`, `iac-nightly.yml` and
  `iac-breakglass.yml` (`name:` to match), in the examples too, so they do not collide with the
  workflows of an application repository (#33). The cockpit's default `workflows:` is now
  `.github/workflows/iac-staged.yml`; an estate that kept `main.yml` names it in its repos file.
- With `--iac-root`, the `paths:` filter of `iac-staged.yml` lists the ayasee callers
  (`.github/workflows/iac-*.yml`), not all of `.github/**`; `ayasee main-tip --check` (R70) now
  counts the same set as a change of the IaC, so a commit the filter skips never supersedes a
  waiting run.
- `ayasee init` can be run again: it skips identical files, ignores empty `.gitkeep` files, and
  prints a diff (and the differing keys of `.ayasee.yaml`) for files that differ, which `--force`
  overwrites.
- R70 (#15): `resolve` in `iac-staged` refuses a commit that is no longer the tip of `main` only
  when a later commit changes the IaC: a file inside `iac_root` (documentation excepted, R38), the
  configuration file, or anything under `.github/`. Otherwise the run goes on and records its own
  SHA. `ayasee main-tip --check SHA [--config-path]` makes the decision through the GitHub compare
  API and refuses on any doubt (an API error, a tip that does not descend from the SHA, a
  comparison of 300 files or more). The monorepo docs now recommend a `paths:` filter on the
  `push` caller of `iac-staged` (still none on the `pull_request` caller of `iac-pr`).
- A `units_root` outside `iac_root` was already refused on every load of `.ayasee.yaml`; when the
  value looks written relative to `iac_root` (`stages`, or no `units_root`, next to
  `iac_root: infra/iac`), the error now says that `units_root` is relative to the repository root
  and, if that was the intent, which path to write (#11).
- The pre-prod jobs of `iac-staged` are four fixed slots, `stage-1` to `stage-4`, instead of `dev`
  and `uat` (#10). Each needs the one before it; the pre-prod stages fill them right-aligned, so
  `stage-4` always runs the stage just before prod and unused slots are skipped like slots before
  `start-stage`. Display names, environments and ledger records still use the stage names. The
  `resolve` outputs and `ayasee config staged` print `run-stage-<i>` and `stage-<i>` instead of
  `run-dev`, `run-uat`, `first-stage` and `pre-prod-stage`. A matrix was not used because GitHub
  does not order matrix jobs; see [the staged run](docs/concepts/staged-run.md#stages).
- Stage names that end in `-plan`, start with `drift-` or are `prod`, `prod-pr` or
  `prod-breakglass` are refused by `ayasee config staged`, `ayasee config pr-plan-environment` and
  the `github-environments` module, so a pull-request plan environment, which accepts every branch,
  can never share a name with an environment that has a fixed role. `binding-mode: same-job` is
  refused for every `*-plan` environment.

#### Added

- `docs/guides/adopt.md` (#11): adopting ayasee in an existing repository on one page, in one
  order: `init`, bootstrap, the printed secret commands, `doctor`, `import-plan`, the first pull
  request and one merge per unit, with the steps only a person can do marked.
- `ayasee init --profile cloudflare-s3|databricks-azure [--stages] [--iac-root] [--source] [--ref]`
  (#11): writes the configuration, the callers, `root.hcl`, the bootstrap root and an empty
  directory per stage, pinned to one release commit: the callers' `uses:` and the bootstrap's
  module sources (`git::https://github.com/<source>.git//modules/<m>?ref=<SHA>`). The commit is
  the head of `release/v<version>` of the installed version, accepted only when its single parent
  is the tagged commit; `--ref` gives it instead and is required from a source checkout. A
  monorepo gets `iac_root`, `units_root` and a `paths:` filter on the `push` caller. Existing files
  are never overwritten without `--force`. The wheel packages the profile templates file by file.
- The Cloudflare and S3 example's bootstrap derives its environments from one `stages` local.
- `github-environments` (#11): a `secret_commands` output prints one `gh secret set` command per
  secret the bootstrap cannot create (provider tokens per environment from `secret_scopes`, the
  plan-key secrets of `prod-plan`, `prod` and `prod-breakglass` from `key_provider`, repository
  secrets from `repository_secret_scopes`), each with the scope its value needs and never a value;
  `pending_secrets` gives the same list as data. The opt-in `generate_plan_passphrase`
  (`key_provider = "pbkdf2"` only) generates `AYASEE_PLAN_PASSPHRASE` with `random_password` (64
  characters of `[A-Za-z0-9]`) and sets it on those three environments; the value is then in the
  bootstrap's state in plain text. Both example bootstraps wire it, so `ayasee init` writes it
  too; the module needs the github provider `~> 6.12` and `hashicorp/random`.
- `ayasee doctor` (#11): checks, before the first run and in one pass, what it needs: the
  configuration (supported settings, job slots, `shared_units`, stage directories), the callers
  (one full release SHA, no placeholder, `iac-staged` and `iac-pr` present, no `paths:` filter on
  the pull-request caller), and on GitHub, read-only with an administrator's token: the
  environments, their reviewers and self-review setting, the `main`-only deployment policies,
  every secret a caller passes in each environment that reads it (environment, not repository,
  secrets for providers and plan keys), the ledger, OIDC and plan-key variables, and a
  pull-request ruleset on `main` when a `ledger-token` is passed. `--lock-check s3://...` runs the
  conditional-write lock check against a state bucket without the AWS CLI. Exit 0 all passed, 1 a
  failure, 2 something could not be checked. The quickstart and the Cloudflare and S3 example run
  it before the first pull request.
- `ayasee import-plan --plan-json <show -json of a plan> [--out imports.tf] [--force]` (#11, import
  help): writes the `import` blocks of a unit's first adoption. For each planned create of a
  supported type it lists the live objects through the provider API (GET requests only, the
  provider's token variable, never printed) and writes one block per address with exactly one
  match; a DNS record other than a CNAME must match on content too. Two live objects for one address, or one object for two addresses, writes nothing and
  exits 1; so does any API error or an ID that is not a plain identifier. Unsupported types and
  values unknown at plan time are listed as `skip`. Lookups exist for the Cloudflare v5 resources of
  the `cloudflare-s3` example (`cloudflare_dns_record`,
  `cloudflare_zero_trust_tunnel_cloudflared_config`, `cloudflare_zero_trust_access_application`
  for account-scoped applications, `cloudflare_zero_trust_access_policy`), in
  `src/ayasee/importplan/cloudflare.py`. Documented in step 7 of the example README and in the
  brownfield onboarding guide.
- Pre-commit hooks (`.pre-commit-config.yaml`, `uv run pre-commit install`; `pre-commit` joins the
  dev group): gitleaks on the staged changes and `detect-private-key`, the standard hygiene hooks of
  `pre-commit-hooks`, ruff check and format, shellcheck, zizmor, actionlint, `tofu fmt`, `opa fmt`
  and `opa check`, the doc-link check, and the unit tests as an opt-in pre-push hook. Remote hook
  repositories are pinned by commit SHA (`# frozen:` tag) and Renovate's `pre-commit` manager is
  enabled. See CONTRIBUTING.md "Pre-commit hooks".
- A `secret scan` CI job: gitleaks over every commit of a pull request (the whole history on a push
  to `main`), through `scripts/check-secrets.sh` with values redacted, and the allowlist tests
  against the real binary. `scripts/install-dev-tools.sh gitleaks` installs gitleaks 8.30.1,
  checksum-verified. `.gitleaks.toml` allowlists only a single-token value marked `ayasee-test` /
  `ayasee-fixture` under `tests/` or `e2e/`; inline `gitleaks:allow` comments are ignored, and a
  rotated finding is recorded by fingerprint in `.gitleaksignore` (CONTRIBUTING.md).
- `ayasee trunk-guard` and a step in the `resolve` job of `iac-staged` and `iac-nightly` (#11): when
  `ledger-token` is passed, the run checks with that token that an active ruleset with a
  `pull_request` rule applies to `main` with no bypass for the token (so it cannot push to `main`
  directly), and warns in the log and the run summary otherwise: `main` unprotected, classic branch
  protection whose bypass is not verifiable, a bypass, or an API error. Read-only calls (Metadata and
  Contents read); no job permission added.
- The `identity: secrets` DEVIATION label now says that stages are isolated only as far as the
  provider token scopes are (#11): a pre-prod token whose scope covers prod objects (a shared DNS
  zone, for example) can change them without the prod gate.
- An **experimental** `s3://` ledger store (#10, item 1; the URL format may change):
  `s3://<bucket>?region=<region>&prefix=<prefix>&endpoint=<https>` for Amazon S3 and
  S3-compatible stores. A small SigV4 client in `src/ayasee/s3.py` (standard
  library and `httpx`, no new dependency) reads credentials from `AWS_*` environment variables.
  The history entry is written with `If-None-Match: *` and `current.json` with `If-Match` on the
  ETag it read, retried up to 5 times on a lost race. Listing
  responses with a DTD are refused in any encoding, response bodies are capped on their
  decompressed size, and the endpoint host is normalised the way it is sent.
- The `s3://` ledger store in the reusable workflows and the cockpit (#10, item 1; still
  experimental, not yet run against a real S3 or R2 bucket). A dedicated ledger key, never the
  state key: the new optional secrets `AYASEE_LEDGER_AWS_ACCESS_KEY_ID` and
  `AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY` of `iac-staged`, `iac-nightly` and `iac-breakglass` reach
  only the steps that read or write the ledger (guard, ledger, and the `ledger show` steps of the
  drift and break-glass runs), as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. No session
  token. `cockpit-collect` gets its own read-only pair, `AYASEE_COCKPIT_AWS_ACCESS_KEY_ID` and
  `AYASEE_COCKPIT_AWS_SECRET_ACCESS_KEY`, for its collect step only; `repos.yaml` accepts `s3://`
  ledgers (the region must be in the URL), and the collector reads every ledger through a
  read-only view that cannot write. The snapshot schema is unchanged. `ledger.url` in
  `.ayasee.yaml` refuses an `s3://` URL with `endpoint=` (even empty): the file is code a pull
  request can change and the endpoint would receive signed requests, so such a URL belongs in the
  `AYASEE_LEDGER_URL` variable. `ayasee doctor` checks the ledger key as an environment secret of
  every environment that reads it, as a pair, and passed when `ledger.url` is `s3://`. The IAM
  policies are in docs/concepts/ledger.md and docs/cockpit/setup.md.
- `plan_encryption.key_provider: aws_kms` (#10): the prod plan key is wrapped by the AWS KMS key
  whose ARN is in the `AYASEE_KMS_KEY_ID` variable (new `kms-key-id` input of the plan and apply
  actions; the region comes from the ARN), with the encryption context `ayasee = plan`. KMS is
  reached with a dedicated long-term IAM key pair, the new optional secrets
  `AYASEE_KMS_ACCESS_KEY_ID` and `AYASEE_KMS_SECRET_ACCESS_KEY` of `iac-staged` and
  `iac-breakglass`, passed only to the prod plan and apply steps, so it works with either
  `identity`. A KMS key or key pair next to another key source is refused as ambiguous. Tested:
  the rendered block, the ARN and key-pair checks, and OpenTofu 1.13.9 accepting the block offline;
  not yet run against a real KMS key.
- `plan_encryption.key_provider: gcp_kms` and `openbao` (#10). `gcp_kms` wraps the prod plan key
  with the Cloud KMS key in the `AYASEE_GCP_KMS_KEY_NAME` variable (no key version), reached with
  the service-account key file in the new optional secret `AYASEE_GCP_KMS_CREDENTIALS`; the file is
  checked to be a service-account key, written to a private file under `RUNNER_TEMP` that the plan
  or apply script deletes on exit, and only its path goes into `TF_ENCRYPTION`. `openbao` wraps it
  with the OpenBao transit key in the `AYASEE_OPENBAO_KEY_URL` variable
  (`https://<host>[:<port>]/v1/<mount>/keys/<name>`), reached with the token in the new optional
  secret `AYASEE_OPENBAO_TOKEN`. New `gcp-kms-key-name` and `openbao-key-url` inputs of the plan
  and apply actions; `iac-staged` and `iac-breakglass` pass the secrets only to the prod plan and
  apply steps, and `ayasee doctor` checks them and the variables in `prod-plan`, `prod` and
  `prod-breakglass`. Any key reference or credential of another provider is refused as ambiguous.
  Tested: the rendered blocks, the checks and refusals, and OpenTofu 1.13.9 accepting each block
  offline up to its first call to the key service; not yet run against a real Cloud KMS key or
  OpenBao server. Workload Identity Federation (GCP) and JWT login (OpenBao) are not wired.
- The cockpit shows the opt-ins that weaken a compliant default (#1): the collector reads each
  repository's `.ayasee.yaml` at `main` (`config` in `repos.yaml`) and the snapshot carries an
  optional `repos[].labels` list (`{kind: non-compliant, setting: solo_mode}`,
  `{kind: deviation, setting: identity}`). The site shows a tag under the repository name, the
  Markdown view a "Labels" list. `ayasee.config.label_keys` and `parse_config_text` back it.
- A second reference repository, `examples/cloudflare-s3/` (#1): stages `[test, prod]`,
  `identity: secrets`, the `pbkdf2` plan key, an S3-compatible backend, a `git-branch://` ledger
  set per environment, and the tunnel ingress and Access application as `shared_units`. Its README
  gives the token scopes per environment and the worked no-cloud ledger setup.
- The solo-maintainer path, end to end (#1): `solo_mode` in the `github-environments` module lets
  the prod and break-glass reviewers be the same person and turns `prevent_self_review` off on
  `prod`, `prod-breakglass` and `prod-pr-plan` (reviewers stay required, no admin bypass); without
  it the module refused a single maintainer outright. A new e2e scenario, `solo`, checks the
  NON-COMPLIANT label, a self-approved prod gate with the maintainer recorded as approver, and a
  self-approved break-glass with its post-approval issue. `Sandbox.review(as_self=True)`,
  `Sandbox.login` and `Sandbox.read_file` support it.
- Adoption notes from the first adopter (#11): a monorepo section in the `cloudflare-s3` README
  (`iac_root: infra/iac`, `units_root: infra/iac/stages`); in the quickstart, a check that an
  S3-compatible store refuses a second `If-None-Match` write, which `use_lockfile` needs; in the
  migration guide, how a repository whose applications use `develop` routes its IaC pull requests
  to `main`. The monorepo notes now also say why the `iac-staged` caller takes no `paths:` filter:
  with the superseded-commit refusal (R70) it can leave an IaC commit unpromoted.
- `iac-staged` runs up to four pre-prod stages (#10), for example `[dev, sit, uat, preprod, prod]`.
- More provider credentials for `identity: secrets` without a change in ayasee (#11): an optional
  `AYASEE_PROVIDER_ENV` secret on every reusable workflow (an environment secret holding one JSON
  object of name to string value) and a `provider_env` list in `.ayasee.yaml` that declares the
  names it may hold. The plan and apply steps run `ayasee provider-env` first: it refuses
  undeclared keys, non-string values, values shorter than 16 characters (their mask would drop
  job outputs such as `has-changes`) and reserved names (`PATH`, `LD_*`, `BASH*` and bash's own
  variables, `NODE_OPTIONS`, `GITHUB_*`, `TF_*` other than `TF_VAR_*`, `TERRAFORM_*`, `AWS_*`,
  `CLOUDFLARE_*`, `AYASEE_*`, TLS and proxy variables, ...), masks every value whole and line by
  line, and hands the pairs to the script through a private file that is deleted before anything
  is exported, so the variables never reach `$GITHUB_ENV`, a command line or a later step. The
  three named tokens are unchanged.
- `environment_prefix` in `.ayasee.yaml` (#31), default empty: it is put in front of the name of every
  GitHub environment ayasee uses, so that ayasee's environments stop colliding with an application's
  own `test` and `prod`. With `iac-` they are `iac-test`, `iac-test-plan`, `iac-prod-plan`, `iac-prod`,
  `iac-prod-breakglass`, `iac-drift-test`, `iac-drift-prod` (and `iac-prod-pr-plan` with
  `shared_units`). Stages, unit paths, ledger records, guard order, drift issues and notifications
  keep the stage names; what GitHub resolves (`environment:`, the deployment mirror, the approvals
  lookup, secrets, variables, OIDC subjects, the cockpit's pending gates) gets the prefix. The
  decision table is in [the reference](docs/reference/ayasee-yaml.md#environment-names). **With the
  setting unset every name is the one used before: nothing changes for a repository that does not
  set it.** New: `ayasee config environments`, `ayasee config pr-plan-environment --logical`, the
  `environments` map of `ayasee config show`, the `env-*` outputs of `ayasee config staged`,
  `ayasee ledger write --environment-prefix` (input `environment-prefix` of the ledger action).
- `github-environments`: variables `environment_prefix` and `adopt_existing_environments`, output
  `environment_names`. The keys of `variables` and `secret_scopes` stay the unprefixed names; the
  `environments`, `pr_plan_environment`, `plan_passphrase_environments` outputs and the `--env` of
  `secret_commands` give the GitHub names. Both example bootstraps take the two variables, and
  `azure-stage-identity` documents that `environment` is the prefixed GitHub name (its subject and
  identity name follow it).
- The bootstrap refuses to take over an environment that already exists and that it did not create
  (#31): applying used to rewrite the reviewers and branch policy of an application's `test` or
  `prod` silently. The module reads the repository's environments, marks every environment it
  creates with the variable `AYASEE_MANAGED`, and fails the plan, naming the environment, when an
  unmarked one exists. Set `environment_prefix`, or `adopt_existing_environments = true` to take one
  over on purpose ([adopt](docs/guides/adopt.md#environments-that-already-exist)).
- `ayasee doctor` checks the environments under their prefixed names, warns about environments
  without the `AYASEE_MANAGED` marker, and warns when a workflow that is not an ayasee caller names an
  environment ayasee uses (`environment:` literals, per job; no token needed).
- `ayasee init --iac-root` sets `environment_prefix: iac-` in `.ayasee.yaml` and as the default of
  the bootstrap variable, because a monorepo is where the names collide; `--environment-prefix`
  sets another prefix or, with an empty value, none.
- `ayasee init --upgrade` moves only the release SHA pinned in the callers and the bootstrap
  sources (#33).
- Docs: why `iac-pr.yml` has no `paths:` filter (a comment in the file), and how to run the staged
  workflow by `workflow_dispatch` only, with what R70 does then.

#### Fixed

- `actions/setup/install.sh` writes the azure extra as `"${src}[azure]"` (shellcheck SC1087; same
  string as before). A final newline, a trailing space and a blank last line in three files, and a
  `tofu fmt` alignment in a test fixture, found by the new hooks.
- The quickstart told adopters without a cloud to use `github-branch://` as their ledger; that
  store is read-only. It now describes the `git-branch://` store, `AYASEE_LEDGER_URL` and
  `ledger-token`.
- `ayasee init --ref <SHA>` refuses a commit that is not the release commit of the version whose
  templates it writes (the head of `release/v<version>`, one commit on top of the tag, with that
  `__version__`), so an rc.2 SHA can no longer sit under rc.3 templates; `--unverified-ref` skips
  the check (#32).
- `ayasee doctor` has a "release pin" check: the callers' pinned commit must be a release commit
  of a version at least the running CLI's; an API error is "could not check" (exit 2) (#32).

### Added

#### Provider-neutral profile, monorepos and shared units

- `iac-staged` runs a configurable stage list (issue #1): `[<first>, <pre-prod>, prod]` or
  `[<pre-prod>, prod]`, with any pre-prod names. The `dev` and `uat` jobs are the two pre-prod
  slots and take their environment, concurrency group, units directory and ledger record from the
  configured names; with two stages the `dev` slot is skipped and the guard of `prod-plan` chains
  from the stage just before prod. `ayasee config staged` validates the layout and `start-stage`.
  `iac-pr` plans the first configured stage. Not yet run on GitHub with a two-stage list.
- `identity: oidc | secrets` in `.ayasee.yaml` (default `oidc`). With `secrets`, the stage jobs of
  every reusable workflow skip `azure/login` and leave the `ARM_*` variables and
  `DATABRICKS_AUTH_TYPE` empty; every load prints a `DEVIATION:` line (D7).
- `plan_encryption.key_provider: azure_vault | pbkdf2` (default `azure_vault`, unchanged). `pbkdf2`
  encrypts the prod plan with a passphrase from the `AYASEE_PLAN_PASSPHRASE` environment secret,
  passed only to the prod plan and apply steps (and those of break-glass); a missing, short or
  ambiguous key source fails closed. The manifest and stale-plan checks are unchanged. The plan and
  apply actions take a `key-provider` input.
- `identity: secrets` passes the provider tokens `CLOUDFLARE_API_TOKEN`, `AWS_ACCESS_KEY_ID` and
  `AWS_SECRET_ACCESS_KEY` (optional `workflow_call` secrets, meant as environment secrets) to the
  plan and apply steps only, and empty strings with `identity: oidc`. `ayasee config labels` prints
  the `NON-COMPLIANT` and `DEVIATION` lines; the first job of every reusable workflow turns them
  into warnings and run-summary lines. The cockpit does not show the label yet.
- A second risk-rule and policy pack, for Cloudflare edge, DNS and Zero Trust Access:
  `rules/cloudflare.yaml` (Access, tunnel ingress, WAF and firewall, proxied, MX and apex DNS
  records; provider v5 and v4 type names) and `policy/cloudflare/` (`SG-CF-001` to `SG-CF-005`,
  with OPA tests). The change set shows principals gained and lost for Access policies and groups.
  Risk rules take a new optional condition, `attr_in`. Both packs always load; the policy action
  runs conftest with both namespaces. Not yet checked against a real Cloudflare plan.
- A reference `root.hcl` for an S3-compatible state backend (`use_lockfile` locking, one bucket per
  stage) in the quickstart's "Without Azure" section. No `s3://` ledger store: `github-branch://`
  remains the ledger that needs no cloud.
- Distribution (#3): the public repository `tttao/ayasee` is the supported source; the quickstart
  pins a published release SHA, shows a pinned local CLI install (`uvx --from git+...@<release SHA>`
  or the attested wheel), and the releasing guide documents mirrors for GHES and air-gapped estates.

- `shared_units` in `.ayasee.yaml` for resources that exist once and serve every stage (#5): they
  live under `<units_root>/prod` only (`ayasee config check-shared`, run by `iac-pr` and
  `iac-staged`), are flagged as shared in the change set and the gate summary
  (`ayasee changeset --shared-units`, plan action input `shared-units`), and are planned read-only
  in pull requests by the new `shared-changes`, `plan-shared` and `comment-shared` jobs of `iac-pr`
  in the opt-in `prod-pr-plan` environment (`shared_units_pr_plan` in `github-environments`).
  `ayasee changed-units --shared` and `ayasee pr-comment --marker pr-plan-shared` support them.

- `iac_root` in `.ayasee.yaml` (default `.`) for monorepos where the IaC sits beside application
  code (#4). The `iac-pr` format checks, Trivy and Checkov, and the nightly rescan, are scoped to
  it; `ayasee changed-units --iac-root DIR --config-path FILE` ignores changes outside it, so an
  application-only pull request plans nothing. `units_root` must be inside `iac_root`, and both
  must be relative paths inside the repository. `ayasee sarif-scrub --uri-prefix DIR` turns the
  scanners' `iac_root`-relative paths back into repository paths.

- A documentation site on GitHub Pages, <https://tttao.github.io/ayasee/>: the `docs` workflow
  assembles the repository's Markdown with `scripts/build-docs-site.py` and builds it with mkdocs
  in strict mode. The landing page and theme are in `website/`.
- `python -m e2e.ephemeral up|pr|status|down`: create and delete a throwaway `*-sandbox` repository
  for trying out and demonstrating ayasee, with no Azure account and no released commit. See
  [the e2e README](e2e/README.md#ephemeral-sandbox-one-command-no-cloud).
- [`docs/governance/decisions.md`](docs/governance/decisions.md): the design decisions D1 to D14
  that the docs and the code cite.

#### Pipeline

- `iac-staged`: one workflow run that promotes dev, uat and prod in order, with environment
  approval gates, supersession and redrive. A run whose commit is no longer the tip of `main` stops
  in `resolve` as `superseded` (R70, `ayasee main-tip`).
- `iac-pr`: PR checks (scans, SARIF with scrubbed snippets, sticky comment) and a read-only dev plan
  for changed units only.
- Composite actions over the `ayasee` CLI: `setup`, `plan`, `apply`, `guard`, `ledger`,
  `notify`, `policy`, `risk`, `cockpit-site`.
- Changed-unit detection; documentation-only changes plan nothing.
- Teams notifications carrying counts and links only.
- Release process: `scripts/set-ayasee-ref.sh` pins the own-action references to the released
  commit; `release.yml` builds, pins, attests and publishes.

#### Safety core

- Plan binding: the prod apply uses the exact approved encrypted plan files, checked by SHA-256
  hash, engine version and provider lock hash. A stale or mismatched plan stops the run and asks
  for a re-plan and a new approval.
- Replan fallback for plain Terraform, equal on `(address, actions)` and a hash of the
  non-sensitive `after` values.
- Backend ledger (file, git-branch and Azure Blob stores) as the source of truth for what is live;
  Deployments is written as a projection. The setup action installs ayasee with the `azure`
  extra, every dependency pinned by version and hash.
- Guard: predecessor, supersession and stop-the-line checks, failing closed.
- Sanitized change set and gate summary; engine output never reaches the job log. Root output
  changes are items of kind `output` (no values), so an output-only prod change goes through the
  gate, counts for the cascade rule and is reported as drift (R72).
- Cascade rule: a unit and a unit that reads its outputs cannot change in the same prod plan.

#### Policy

- Tiered Databricks and Azure risk classifier with principal deltas, and adopter rule files.
- Databricks Rego policy pack `SG-DBX-001` to `SG-DBX-006` on plan JSON.
- `.ayasee.yaml` with compliant defaults: `prevent_self_review`, no admin bypass, `solo_mode`
  opt-in and logged as non-compliant.

#### Drift

- `iac-nightly`: drift detected at the live SHA, one issue per environment, noise paths, lock
  timeout, prod drift holds prod.

#### Break-glass

- `iac-breakglass`: separate `prod-breakglass` environment with its own reviewers, a required
  incident reference, the whole prod tree planned and applied, a fresh-plan check against the
  policy-checked preview and a post-approval issue.
- DORA Art. 17 compliance mapping.

#### Cockpit

- Read-only collector producing snapshot schema `ayasee.snapshot/v1`, with ETag cache and
  scoped tokens.
- Static estate site (repositories by environments), detail drawer, history and Markdown renderer.
- `cockpit-collect` workflow and data repository layout.

#### Bootstrap

- OpenTofu modules for GitHub environments and Azure stage identities (managed identity and
  federated credential per environment).

#### E2E harness

- Sandbox scenarios, driver and `e2e.yml`, unit-tested against fakes.

#### GHES

- `ayasee features` platform and feature detection (github.com, GHE.com, GHES) and a
  compatibility suite under `e2e/ghes/`.

#### Documentation

- Concepts, guides, reference, comparison, DORA mapping and a Databricks on Azure example
  repository.

### Changed

- Releases: the maintainer pushes `release/<tag>` with the new `scripts/pin-release.sh`, before the
  tag; the release workflow makes the same commit and only verifies the pushed branch (parent and
  tree). A GitHub Actions token cannot push a change to `.github/workflows/`, so the workflow's own
  push always failed. The `pin` job now needs only `contents: read`.
- `iac-staged`'s `start-stage` input defaults to empty (the first configured stage) instead of
  `dev`; `dev`, `uat` and `prod-plan` keep working with the default stages.
- `binding-mode: same-job` is allowed for any pre-prod stage, not only `dev` and `uat`; it is still
  refused for `prod`, `prod-plan`, `prod-breakglass`, `dev-plan` and `drift-*`.

- The licence is settled: Apache-2.0, stated in
  [`docs/governance/PROVENANCE.md`](docs/governance/PROVENANCE.md). `scripts/check-provenance.sh`,
  the first job of the release, now checks that this statement, `pyproject.toml` and `LICENSE`
  agree and that `NOTICE` is present. `CONTRIBUTING.md` states the licence of contributions.

### Fixed

- `python -m e2e.ephemeral up` sets `AYASEE_LEDGER_URL` on the `prod-breakglass` environment.
  Break-glass reads the ledger URL from its environment only, so it failed at its first step in an
  ephemeral sandbox.
- `state-sync.sh` resolves a relative state directory against the hook directory, not the
  repository top.
- Documentation links to a report that is not part of the repository are gone.

- The e2e fixture's `state-sync.sh` resolved git paths against the Terragrunt hook directory
  (`.terragrunt-cache/...`), so restoring saved state failed and every converge check after the
  first apply broke. Found by the first live sandbox run.
- The setup and policy actions retry tool downloads for about 90 seconds instead of 7: GitHub
  release downloads return 504 for minutes at a time.

### Known limitations

- **GHES-specific behaviour is unverified.** The workflows target github.com first. On GHES every
  feature flag is false until a verified entry exists, and nothing has run on a real GHES.
- **GitHub Enterprise Cloud with data residency (`ghec-dr`) is detected but unverified.** The
  platform comes from the API host (`api.<sub>.ghe.com`); every feature flag stays false until
  verified.
- **The `features` output is not consumed yet.** `iac-staged` exposes it, but no workflow reads it.
- **The e2e sandbox has not been run.** The scenarios are implemented and unit-tested against
  fakes; the first real run may need fixes.
- **Settings accepted but not supported yet are refused (R35, R73).** `gate: risky`,
  `engine: terraform`, `terragrunt: false`, a non-empty `extra_rules` and `notify.teams: false` make
  every reusable workflow fail in its first job with "<key>: not supported yet"
  (`ayasee config check-supported`). Prod always asks for approval.
- **Cascades need sequential merges.** `prod-plan` refuses a plan in which a unit and a unit that
  reads its outputs both change; this includes first-time adoption of dependent units.
- **Dependents of changed units are not planned in PRs** unless a change outside `stages/dev/`
  makes the PR plan every dev unit.
- **Plain Terraform is not wired into the workflows.** `engine: terraform` is refused (see
  above); the D1(b) replan fallback exists in the actions and the CLI only.
- **Azure first.** Entra identities, Azure Key Vault plan encryption and the `azure-blob://`
  ledger store are the wired pieces.
- **`extra_rules` is not forwarded by the workflows.** Adopter risk rule files work in the `risk`
  action and the CLI only; the workflows refuse a non-empty list.
- **Break-glass has no unit scope (R71).** It always plans and applies the whole prod tree.
- **The cockpit reads only `github-branch://` ledgers.** `cockpit-collect` has no cloud login; a
  repository with an `azure-blob://` ledger cannot appear in it.
- **Not a write UI.** The cockpit is read-only.
- **Single maintainer.** The project is pre-alpha with one maintainer; see
  [GOVERNANCE.md](GOVERNANCE.md). The licence is Apache-2.0
  ([`docs/governance/PROVENANCE.md`](docs/governance/PROVENANCE.md)).

[Unreleased]: https://github.com/tttao/ayasee/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tttao/ayasee/releases/tag/v0.1.0
