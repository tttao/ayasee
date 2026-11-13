# Changelog

All notable changes to ayasee are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/). The release workflow copies the section of the
released version into the GitHub release notes.

## [Unreleased]

### Added

- `ayasee trunk-guard` and a step in the `resolve` job of `iac-staged` and `iac-nightly` (#11): when
  `ledger-token` is passed, the run checks with that token that an active ruleset with a
  `pull_request` rule applies to `main` with no bypass for the token (so it cannot push to `main`
  directly), and warns in the log and the run summary otherwise: `main` unprotected, classic branch
  protection whose bypass is not verifiable, a bypass, or an API error. Read-only calls (Metadata and
  Contents read); no job permission added.
- The `identity: secrets` DEVIATION label now says that stages are isolated only as far as the
  provider token scopes are (#11): a pre-prod token whose scope covers prod objects (a shared DNS
  zone, for example) can change them without the prod gate.
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

### Changed

- A `units_root` outside `iac_root` was already refused on every load of `.ayasee.yaml`; when the
  value looks written relative to `iac_root` (`stages`, or no `units_root`, next to
  `iac_root: infra/iac`), the error now says that `units_root` is relative to the repository root
  and names the path to write (#11).

### Fixed

- The quickstart told adopters without a cloud to use `github-branch://` as their ledger; that
  store is read-only. It now describes the `git-branch://` store, `AYASEE_LEDGER_URL` and
  `ledger-token`.

## [0.1.0] - unreleased

First release (pre-alpha), published first as the release candidate `v0.1.0-rc.2` (the run of
`v0.1.0-rc.1` failed before publishing anything). The date is set when the final version is tagged, see [releasing](docs/guides/releasing.md).

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
