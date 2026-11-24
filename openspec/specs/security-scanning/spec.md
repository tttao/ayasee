# security-scanning Specification

## Purpose

Static scanning of IaC with Trivy and Checkov on pull requests, the prod plan and a nightly re-scan
of `main`, published as code-scanning alerts through SARIF that is rebuilt from an allow-list so it
never carries values (D10). Code: `src/ayasee/sarif.py`, `src/ayasee/findings.py`.
## Requirements
### Requirement: SARIF rebuilt from an allow-list
`ayasee sarif-scrub --in F --out F` SHALL rebuild the file keeping only `version`, `$schema`, the tool driver and extensions (name, version, rules with id, name, short description, default level), each result's rule reference, level, kind, partial fingerprints (derived from rule, file and line when absent), locations reduced to file URI plus line and column, and per-run base URIs; `result.message.text` SHALL always be the rule's short description (or rule id); snippets, region messages, code flows, fixes, properties and anything else SHALL be dropped.

#### Scenario: Finding with a code snippet
- **WHEN** a scanner result contains a snippet of a secret value
- **THEN** the scrubbed SARIF contains no snippet and the message is the rule's short description

### Requirement: Monorepo path rewriting
With `iac_root` other than `.`, `sarif-scrub --uri-prefix <iac_root>` SHALL rewrite scanner paths relative to `iac_root` into repository paths, and Checkov's `github_actions` framework SHALL run only when `iac_root` is `.`.

#### Scenario: Finding in infra/iac
- **WHEN** Trivy reports `modules/x/main.tf` while scanning `infra/iac`
- **THEN** the alert points at `infra/iac/modules/x/main.tf`

### Requirement: Only scrubbed output leaves the job
Only scrubbed SARIF SHALL leave a scanning job, and job logs SHALL list rule ids and file names only; on the prod plan the Trivy report SHALL be written to a file that is never uploaded.

#### Scenario: prod-plan Trivy
- **WHEN** Trivy scans the prod plan JSON
- **THEN** only rule ids and severities are printed

### Requirement: Checkov baseline
When `.ayasee/checkov.baseline` exists, the PR and nightly scans SHALL pass `--baseline .ayasee/checkov.baseline` so only new findings fail.

#### Scenario: Accepted existing finding
- **WHEN** a finding recorded in the baseline is still present
- **THEN** Checkov does not fail on it

### Requirement: Nightly re-scan of main
The `rescan` job of `iac-nightly` SHALL run Trivy (exit code 0) and Checkov (`--soft-fail`) on `main`, scrub and upload SARIF with categories `ayasee-trivy` and `ayasee-checkov`, and may hold `security-events: write` in the same job because it scans only merged code.

#### Scenario: Findings on main
- **WHEN** the re-scan finds issues
- **THEN** the run does not fail and the findings become code-scanning alerts

### Requirement: New findings window
After the upload, `ayasee findings new --since-hours 26` SHALL count open high or critical code-scanning alerts created in the window, fail on API error, and trigger a `new_findings` notification with counts and links only when the count is above zero.

#### Scenario: One new critical
- **WHEN** one critical alert was created in the last 26 hours
- **THEN** Teams gets a `new_findings` card with the count

### Requirement: A scanner that writes no report fails as a scanner failure
When a scanner exits without writing its report (a crash, such as a Trivy panic), the step that reads the report SHALL fail with an error saying the scanner wrote no report and pointing at the scanner's step, and the job SHALL fail; it SHALL never pass silently nor end in an unexplained `FileNotFoundError`. `ayasee sarif-scrub` SHALL exit non-zero with such a message when its input file does not exist. This SHALL hold in `iac-pr`, in the `rescan` job of `iac-nightly` and for the Trivy scan of `prod-plan`, and the message SHALL carry no value from the scanned code.

#### Scenario: Trivy panics in the PR checks
- **WHEN** Trivy crashes before writing `trivy.raw.sarif`
- **THEN** the Trivy scrub step fails saying Trivy wrote no report, Checkov still runs, and `checks` fails

#### Scenario: Trivy panics on the prod plan
- **WHEN** Trivy crashes before writing its report on the prod plan JSON
- **THEN** the step that lists Trivy findings fails saying Trivy wrote no report

