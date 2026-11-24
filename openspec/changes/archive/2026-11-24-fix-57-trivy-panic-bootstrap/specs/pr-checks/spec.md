## MODIFIED Requirements

### Requirement: Static checks and scanners
The `checks` job SHALL run `tofu fmt -check -recursive`, `terragrunt hcl format --check`, `terragrunt hcl validate`, Trivy `config` and Checkov (`uvx checkov@<version>`, with `--baseline .ayasee/checkov.baseline` when present) scoped to `iac_root`, SHALL run both scanners even after an earlier failure, SHALL scrub each SARIF with `ayasee sarif-scrub`, keep them as artifact `ayasee-sarif`, and fail if any step failed. A scanner that wrote no SARIF (it crashed) SHALL fail its scrub step with a message saying so. A separate `upload-sarif` job SHALL upload them (categories `ayasee-trivy`, `ayasee-checkov`) without checking out code, skipped for forks.

#### Scenario: Format failure plus findings
- **WHEN** `tofu fmt` fails and Checkov reports findings
- **THEN** both SARIF files are still produced and `checks` fails

#### Scenario: Trivy crash
- **WHEN** Trivy panics and writes no SARIF
- **THEN** the Trivy scrub step fails saying Trivy wrote no report, the Checkov SARIF is still produced, and `checks` fails
