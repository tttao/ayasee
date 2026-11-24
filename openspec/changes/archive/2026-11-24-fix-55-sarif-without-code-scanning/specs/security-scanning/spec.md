## MODIFIED Requirements

### Requirement: Nightly re-scan of main
The `rescan` job of `iac-nightly` SHALL run Trivy (exit code 0) and Checkov (`--soft-fail`) on `main`, scrub and upload SARIF with categories `ayasee-trivy` and `ayasee-checkov`, and may hold `security-events: write` in the same job because it scans only merged code. `iac-nightly` SHALL take a boolean input `code-scanning`, default `true`; with `false` the `rescan` job SHALL NOT upload SARIF, SHALL keep the scrubbed SARIF as the run artifact `ayasee-rescan-sarif` (30 days), and SHALL emit a notice and a job summary saying code scanning is off, that no `new_findings` notification is sent, and the number of findings per scanner (counts only).

#### Scenario: Findings on main
- **WHEN** the re-scan finds issues
- **THEN** the run does not fail and the findings become code-scanning alerts

#### Scenario: Code scanning off
- **WHEN** the caller passes `code-scanning: false` and the re-scan finds issues
- **THEN** nothing is uploaded, the run does not fail, the SARIF is in `ayasee-rescan-sarif` and the summary gives the counts

### Requirement: New findings window
After the upload, `ayasee findings new --since-hours 26` SHALL count open high or critical code-scanning alerts created in the window, fail on API error, and trigger a `new_findings` notification with counts and links only when the count is above zero. With `code-scanning: false` the count and the notification SHALL be skipped, said in the job summary, never read as a non-zero count.

#### Scenario: One new critical
- **WHEN** one critical alert was created in the last 26 hours
- **THEN** Teams gets a `new_findings` card with the count

#### Scenario: Code scanning off
- **WHEN** the caller passes `code-scanning: false`
- **THEN** `ayasee findings new` does not run and no `new_findings` card is sent
