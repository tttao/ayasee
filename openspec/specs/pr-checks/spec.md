# pr-checks Specification

## Purpose

`iac-pr` (`.github/workflows/iac-pr.yml`) is the reusable pull-request workflow: format and
validation checks, scanners with scrubbed SARIF, a read-only plan of the changed units of the first
stage, the policy pack on that plan, and one sticky advisory comment. It never applies. Code:
`src/ayasee/changed.py`, `src/ayasee/prcomment.py`. Reference: `docs/concepts/pr-checks.md`.

## Requirements

### Requirement: Never apply, never write from PR code
`iac-pr` SHALL perform no apply, guard or ledger write, SHALL upload nothing the plan produced (only scrubbed SARIF and comment text, kept one day), and no job that evaluates the pull request's HCL SHALL hold `pull-requests: write` or `security-events: write` (R39); results SHALL be handed to small jobs that run no PR code.

#### Scenario: Comment job
- **WHEN** the `comment` job posts the sticky comment
- **THEN** it has checked out only the base commit with `persist-credentials: false` and has no OIDC token

### Requirement: Only the pull_request event
Every job SHALL act only when `github.event_name` is `pull_request` (never `pull_request_target`).

#### Scenario: Called from pull_request_target
- **WHEN** a caller invokes `iac-pr` on `pull_request_target`
- **THEN** its jobs do nothing

### Requirement: Static checks and scanners
The `checks` job SHALL run `tofu fmt -check -recursive`, `terragrunt hcl format --check`, `terragrunt hcl validate`, Trivy `config` and Checkov (`uvx checkov@<version>`, with `--baseline .ayasee/checkov.baseline` when present) scoped to `iac_root`, SHALL run both scanners even after an earlier failure, SHALL scrub each SARIF with `ayasee sarif-scrub`, keep them as artifact `ayasee-sarif`, and fail if any step failed. A separate `upload-sarif` job SHALL upload them (categories `ayasee-trivy`, `ayasee-checkov`) without checking out code, skipped for forks.

#### Scenario: Format failure plus findings
- **WHEN** `tofu fmt` fails and Checkov reports findings
- **THEN** both SARIF files are still produced and `checks` fails

### Requirement: Pull-request plan environment
The `resolve` job SHALL compute the plan environment `<environment_prefix><first stage>-plan` with `ayasee config pr-plan-environment`, and SHALL fail when the configuration does not load, the first stage is `prod` or a stage name is reserved; `plan-first-stage` SHALL then be skipped and the comment SHALL say "the configuration was refused".

#### Scenario: Refused configuration
- **WHEN** `.ayasee.yaml` in the PR does not load
- **THEN** `plan-dev` fails and the comment says the configuration was refused

### Requirement: Read-only plan of changed first-stage units
`plan-first-stage` SHALL check out the PR head with `persist-credentials: false`, plan only the changed units of the first stage with `binding-mode: same-job`, `encrypt: false`, `lock: false`, keep plan files on the runner, run the policy pack with that stage, and write the comment text headed "Plan of `<stage>`".

#### Scenario: State lock
- **WHEN** a PR plan runs while a staged apply holds the lock
- **THEN** the PR plan does not take or wait for the lock

### Requirement: Changed-unit detection
`ayasee changed-units` SHALL print a comma list of changed units of the stage; an empty line when nothing or only documentation (`*.md`, `docs/**`, `LICENSE`) changed; and `ALL` when a non-documentation file inside `iac_root` but outside the stage directory changed, a changed file under the stage belongs to no unit, the configuration file changed, or the diff cannot be computed. Files outside `iac_root` SHALL be ignored, and dependents of a changed unit SHALL NOT be added.

#### Scenario: Module change
- **WHEN** a PR changes a file under `modules/`
- **THEN** every first-stage unit is planned

#### Scenario: Application-only change in a monorepo
- **WHEN** `iac_root` is `infra/iac` and a PR changes only `app/`
- **THEN** no plan runs and the comment says there are no IaC changes

#### Scenario: Detection fails
- **WHEN** change detection itself fails
- **THEN** the comment says the plan failed before it started and never claims there are no IaC changes

### Requirement: Required verdict job
A `plan-dev` job with no permissions SHALL run whenever `resolve` ran and SHALL fail unless both `resolve` and `plan-first-stage` succeeded, so that a skipped plan can never satisfy branch protection.

#### Scenario: Skipped plan
- **WHEN** `plan-first-stage` is skipped because `resolve` refused the configuration
- **THEN** `plan-dev` fails

### Requirement: Sticky advisory comment
The system SHALL keep one comment per PR, found by the marker `<!-- ayasee:pr-plan -->` and taken over only when authored by the expected bot login, containing the change-set summary without resource values and with markup in addresses neutralised.

#### Scenario: New push
- **WHEN** a new commit is pushed to the PR
- **THEN** the existing sticky comment is updated in place

### Requirement: Fork pull requests
For a pull request from a fork, `resolve`, `plan-first-stage`, `plan-dev` and `upload-sarif` SHALL be skipped, while format, validation, Trivy and Checkov SHALL still run and fail the check on failure.

#### Scenario: Fork PR
- **WHEN** a fork opens a pull request
- **THEN** no cloud login happens and the static checks still gate the PR
