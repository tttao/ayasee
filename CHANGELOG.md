# Changelog

All notable changes to ayasee are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/). The release workflow copies the section of the
released version into the GitHub release notes.

## [Unreleased]

### Added

- A documentation site on GitHub Pages, <https://tttao.github.io/ayasee/>: the `docs` workflow
  assembles the repository's Markdown with `scripts/build-docs-site.py` and builds it with mkdocs
  in strict mode. The landing page and theme are in `website/`.
- `python -m e2e.ephemeral up|pr|status|down`: create and delete a throwaway `*-sandbox` repository
  for trying out and demonstrating ayasee, with no Azure account and no released commit. See
  [the e2e README](e2e/README.md#ephemeral-sandbox-one-command-no-cloud).
- [`docs/governance/decisions.md`](docs/governance/decisions.md): the design decisions D1 to D14
  that the docs and the code cite.

### Changed

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

## [0.1.0] - unreleased

First release (pre-alpha). The date is set when the version is bumped, see
[releasing](docs/guides/releasing.md).

### Added

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
