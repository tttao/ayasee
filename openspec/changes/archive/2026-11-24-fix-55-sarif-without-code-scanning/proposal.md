## Why

On a private repository without GitHub Code Security (every private repository on GitHub Free
or Pro, and most on Team), GitHub refuses SARIF uploads ("Code scanning is not enabled for this
repository"). `iac-pr`'s `upload-sarif` job is then red on every pull request and `iac-nightly`'s
`rescan` fails on its upload and on `ayasee findings new`, and neither workflow has a way to turn
the upload off (issue #55, seen on v0.1.0-rc.5).

## What Changes

- `iac-pr` and `iac-nightly` get a boolean input `code-scanning` (default `true`, today's
  behaviour). With `false`: `upload-sarif` is skipped; the rescan uploads nothing and skips
  `ayasee findings new` and the `new_findings` notification; the scrubbed SARIF is kept as a run
  artifact (30 days); each job summary says code scanning is off, why, and the finding counts.
  Trivy and Checkov still gate the `checks` job.
- `ayasee init` reads whether the repository can take a SARIF upload (new
  `src/ayasee/codescanning.py`) and writes `code-scanning: false` into the `iac-pr` and
  `iac-nightly` callers when it cannot, or when it reads a private repository on Free, Pro or Team
  and cannot confirm Code Security. `--code-scanning` / `--no-code-scanning` override.
  `init --upgrade` refuses the flag (pins only).
- `ayasee doctor` warns when a caller uploads SARIF and code scanning is unavailable or cannot be
  confirmed for the repository, and says how to turn it off.
- Docs: pr-checks, drift (re-scan), adoption guide plan table, CHANGELOG.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `pr-checks`: the `upload-sarif` job and the artifact retention depend on `code-scanning`.
- `security-scanning`: the nightly re-scan and the new-findings window depend on `code-scanning`.
- `adoption-tooling`: `init` chooses `code-scanning`; `doctor` checks it.

## Impact

- `.github/workflows/iac-pr.yml`, `.github/workflows/iac-nightly.yml` (new input; no change for a
  caller that does not pass it).
- `src/ayasee/codescanning.py` (new), `src/ayasee/github.py` (one read-only probe),
  `src/ayasee/scaffold.py`, `src/ayasee/doctor.py`.
- Callers still grant `security-events: write`: a called workflow's jobs may not ask for more than
  the caller grants, whether they run or not.
