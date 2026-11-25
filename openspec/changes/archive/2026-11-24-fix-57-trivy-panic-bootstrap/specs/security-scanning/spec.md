## ADDED Requirements

### Requirement: A scanner that writes no report fails as a scanner failure
When a scanner exits without writing its report (a crash, such as a Trivy panic), the step that reads the report SHALL fail with an error saying the scanner wrote no report and pointing at the scanner's step, and the job SHALL fail; it SHALL never pass silently nor end in an unexplained `FileNotFoundError`. `ayasee sarif-scrub` SHALL exit non-zero with such a message when its input file does not exist. This SHALL hold in `iac-pr`, in the `rescan` job of `iac-nightly` and for the Trivy scan of `prod-plan`, and the message SHALL carry no value from the scanned code.

#### Scenario: Trivy panics in the PR checks
- **WHEN** Trivy crashes before writing `trivy.raw.sarif`
- **THEN** the Trivy scrub step fails saying Trivy wrote no report, Checkov still runs, and `checks` fails

#### Scenario: Trivy panics on the prod plan
- **WHEN** Trivy crashes before writing its report on the prod plan JSON
- **THEN** the step that lists Trivy findings fails saying Trivy wrote no report
