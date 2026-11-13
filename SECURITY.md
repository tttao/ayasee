# Security policy

ayasee runs with credentials that can change production infrastructure, so its own security
matters more than most projects'. This page says how to report a problem, what is supported, and what
the design does and does not protect against.

## Reporting a vulnerability

Please **do not open a public issue** for a vulnerability.

- Use GitHub's private vulnerability reporting: the repository's **Security** tab, then
  **Report a vulnerability**.
- There is no separate security contact address: the project has a single maintainer
  (see [GOVERNANCE.md](GOVERNANCE.md)).

Include the affected file or workflow, the commit SHA, what an attacker needs (for example the ability to
open a pull request, or a stolen token), and what they gain. Please never include real secrets or
real plan output in a report.

You can expect an acknowledgement, a fix or a mitigation, and a credit if you want one. There is no
bug bounty. Because the project is pre-alpha with a very small team, response times are best effort.

## Supported versions

ayasee is **pre-alpha** and has no published release. Only the latest commit on the default branch
is supported. Callers pin ayasee by commit SHA, so fixes reach an adopter only when they move
that pin; the changelog of a fix will say which workflows and actions it touches.

## Threat model summary

### What is trusted

- The default branch (`main`) of the adopting repository, after review and required checks.
- GitHub itself: its OIDC issuer, its environments and protection rules, and its approvals API.
- The pinned ayasee commit and the pinned third-party actions (all pinned by commit SHA).

### What is not trusted

- **Pull-request code.** Anyone who can open a pull request controls the HCL, modules and providers
  that `iac-pr` evaluates. The design assumes that code runs hostile.
- Anything a pull request produces: its plan output, its comment text, its SARIF.
- Fork pull requests, which get a read-only token and no OIDC token.

### Controls

**PR code is untrusted (R39, R40).**
- `iac-pr` runs on `pull_request`, never `pull_request_target`, and the jobs check the event name.
- Pull requests only ever get a dev plan identity: read-only roles, plans with `-lock=false`, in the
  separate `dev-plan` environment. The workflow reads no uat or prod variable and no secret.
- **The job split (R39).** No job that evaluates PR code (`terragrunt hcl validate`, plan) holds
  `pull-requests: write` or `security-events: write`. The comment and the SARIF upload run in small
  separate jobs that run no PR code and only consume artifacts.
- Stage workflows never use a dependency cache (R40), so PR code cannot poison a cache that a prod job
  later restores.
- The pull-request comment is advisory. The PR's own code produces the plan it summarises.

**No values leave jobs (D8, D10).**
- Plan files and plan JSON stay inside the job that made them. The only artifact from `prod-plan` is
  the OpenTofu-encrypted plan files and a manifest of hashes (AES-GCM, `enforced = true`, key in Key
  Vault).
- Change sets, summaries, comments, Teams cards, issues and the cockpit snapshot carry counts,
  addresses and links only. Sensitive and unknown values are never read into them, and a leak test
  pins this.
- SARIF is rebuilt from an allow-list (`ayasee sarif-scrub`): snippets, messages, help text and
  anything not listed are dropped.
- Engine output goes to `$RUNNER_TEMP`, never to the job log.
- **State is the exception.** Remote state, prod state above all, and the encrypted plan artifact
  do contain secret values; they are protected by storage RBAC (one state container per stage) and
  the Key Vault key. The "no values" claim covers outputs and artifacts that leave a job, not state.

**OIDC only (D7).** No cloud secrets are stored. Each environment has its own Entra user-assigned managed
identity with a federated credential for `environment:<name>`. `ARM_USE_OIDC=true` and
`DATABRICKS_AUTH_TYPE=github-oidc-azure` replace keys. The only optional secrets are a Teams webhook and
a fine-grained `ledger-token` for a `git-branch://` ledger, and they are declared explicitly, never
inherited.
`identity: secrets` in `.ayasee.yaml` is the documented exception, for providers that cannot federate
with GitHub OIDC: the workflows skip `azure/login` and pass the provider tokens `CLOUDFLARE_API_TOKEN`,
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` to the plan and apply steps only. They must be
environment secrets, one set per environment, never repository or organisation secrets. Every
configuration load prints a `DEVIATION:` line and each run shows it as a warning and in its summary,
in the same way `solo_mode` is labelled. Stored tokens are long-lived, and the `dev-plan` ones are
reachable by same-repository pull-request code: keep them read-only. Other provider variables
travel in one more environment secret, `AYASEE_PROVIDER_ENV` (JSON), limited to the names that
`provider_env` in `.ayasee.yaml` declares; names that change how the job runs or where the named
credentials go (`LD_*`, `BASH_ENV`, `GITHUB_*`, `TF_CLI_ARGS`, `AWS_*`, `CLOUDFLARE_*`, proxies,
...) are refused and every value is masked. Protect `.ayasee.yaml` with `CODEOWNERS`. See
[identity: secrets](docs/concepts/staged-run.md#identity-secrets).

**Prod needs a human who is not the author (D2).**
- The `prod` environment has required reviewers, `prevent_self_review = true` and admins cannot bypass.
  `solo_mode` is opt-in and logs a non-compliance line on every run.
- A stale or mismatched plan stops the run. An approved run older than the newest uat SHA is rejected as
  `superseded` by the guard at apply time.
- Break-glass uses a separate environment and reviewers, requires an incident reference and a commit
  that is an ancestor of `main`, runs the policy pack, and always opens a post-approval issue.

**Supply chain.** Third-party actions are pinned by commit SHA; callers pin ayasee by SHA; callers
never use `secrets: inherit`. Tools downloaded by the actions are verified by SHA-256. CI runs
actionlint and zizmor on the workflows.

**Cockpit.** The collector's read token is read-only and limited to the repositories it lists; its write
token is limited to the data repository (R62). Hosting is a confidentiality decision: see
[the cockpit](docs/concepts/cockpit.md#visibility-d8).

### Known gaps

- **Read-only is not secret-safe.** A pull request's plan job can read dev state with the dev-plan
  identity. Keep secrets out of dev state, and keep dev data non-sensitive.
- The `binding-mode: breakglass` check in the actions is a consistency check, not an authorisation
  boundary. The boundaries are the environment's reviewers and branch policy and the identity's
  federated credential subject.
- A workflow is only as safe as the repository settings behind it. Environment protection, branch
  policy and the reviewer sets must be created (the `github-environments` module does).
- GitHub Enterprise Server behaviour is unverified, and the end-to-end sandbox scenarios have not run
  against a real repository ([known limitations](docs/concepts/architecture.md#known-limitations)).
- The Databricks policy pack and risk rules are a defence in depth, not a complete model of every
  dangerous change.
