## MODIFIED Requirements

### Requirement: Never apply, never write from PR code
`iac-pr` SHALL perform no apply, guard or ledger write, SHALL upload nothing the plan produced (only scrubbed SARIF and comment text, kept one day, or 30 days for the SARIF when `code-scanning` is false), and no job that evaluates the pull request's HCL SHALL hold `pull-requests: write` or `security-events: write` (R39); results SHALL be handed to small jobs that run no PR code.

#### Scenario: Comment job
- **WHEN** the `comment` job posts the sticky comment
- **THEN** it has checked out only the base commit with `persist-credentials: false` and has no OIDC token

### Requirement: Static checks and scanners
The `checks` job SHALL run `tofu fmt -check -recursive`, `terragrunt hcl format --check`, `terragrunt hcl validate`, Trivy `config` and Checkov (`uvx checkov@<version>`, with `--baseline .ayasee/checkov.baseline` when present) scoped to `iac_root`, SHALL run both scanners even after an earlier failure, SHALL scrub each SARIF with `ayasee sarif-scrub`, keep them as artifact `ayasee-sarif`, and fail if any step failed. A separate `upload-sarif` job SHALL upload them (categories `ayasee-trivy`, `ayasee-checkov`) without checking out code, skipped for forks and when the caller passes `code-scanning: false`.

#### Scenario: Format failure plus findings
- **WHEN** `tofu fmt` fails and Checkov reports findings
- **THEN** both SARIF files are still produced and `checks` fails

## ADDED Requirements

### Requirement: Code scanning can be turned off
`iac-pr` SHALL take a boolean input `code-scanning`, default `true`. With `true`, `upload-sarif` SHALL run as before and the `ayasee-sarif` artifact SHALL be kept one day. With `false`, the `upload-sarif` job SHALL be skipped (no step holding `security-events: write` runs), the `ayasee-sarif` artifact SHALL be kept 30 days, and the `checks` job SHALL emit a notice and write a job summary that says code scanning is off, that the SARIF is in the `ayasee-sarif` artifact, and the number of findings per scanner (counts only); the verdict of `checks` SHALL still come from Trivy, Checkov and the other checks.

#### Scenario: Private repository without Code Security
- **WHEN** the caller passes `code-scanning: false` and Checkov reports a finding
- **THEN** `upload-sarif` is skipped, `checks` fails on the finding, and its summary says code scanning is off with the Checkov count

#### Scenario: Caller passes nothing
- **WHEN** a caller does not pass `code-scanning`
- **THEN** `upload-sarif` uploads both SARIF files as before
