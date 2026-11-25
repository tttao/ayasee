## ADDED Requirements

### Requirement: Code scanning chosen from the repository
`ayasee init` SHALL read whether the repository can take a SARIF upload (a public repository can; otherwise `security_and_analysis` showing GitHub Code Security or Advanced Security enabled or disabled, else one read of the code-scanning alerts API, whose "not enabled" refusal means unavailable) and SHALL write `code-scanning: false` into the `iac-pr` and `iac-nightly` callers when it is unavailable, or when it cannot be confirmed for a private repository whose plan is Free, Pro or Team (read or passed with `--plan`), with a note saying why; it SHALL leave the callers uploading (the default) when code scanning is available or when nothing says the repository is private, with a note when it could not confirm. `--code-scanning` and `--no-code-scanning` SHALL override, with a warning when they contradict what was read; `init --upgrade` SHALL refuse them.

#### Scenario: Private repository on GitHub Free
- **WHEN** `ayasee init` reads a private repository whose code-scanning API answers "Code scanning is not enabled for this repository"
- **THEN** the `iac-pr` and `iac-nightly` callers pass `code-scanning: false` and init prints a note saying the SARIF stays a run artifact

#### Scenario: Public repository
- **WHEN** `ayasee init` reads a public repository
- **THEN** the callers pass no `code-scanning` input and the SARIF is uploaded

### Requirement: Doctor checks code scanning
When an `iac-pr` or `iac-nightly` caller does not pass `code-scanning: false`, `ayasee doctor` SHALL check, with `GITHUB_TOKEN`, that the repository can take a SARIF upload, report ok when it can, and warn, naming the callers and how to turn the upload off, when code scanning is unavailable or cannot be confirmed (an API error is never reported as ok); without a token or repository it SHALL report the check as skipped. When every such caller passes `code-scanning: false` it SHALL report ok without calling the API and say that findings stay run artifacts.

#### Scenario: Upload on a repository without code scanning
- **WHEN** the `iac-pr` caller passes no `code-scanning` input and the code-scanning API answers "Code scanning is not enabled for this repository"
- **THEN** doctor warns that `upload-sarif` fails on every pull request and names `code-scanning: false`

#### Scenario: Code scanning unconfirmed
- **WHEN** the code-scanning API answers an error that does not say whether it is enabled
- **THEN** doctor warns that it cannot confirm code scanning, never ok
