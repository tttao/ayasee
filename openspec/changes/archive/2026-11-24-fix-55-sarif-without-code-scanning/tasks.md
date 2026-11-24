## 1. Detection

- [x] 1.1 Failing tests for `codescanning.detect` (public, security_and_analysis, API refusals, unknowns)
- [x] 1.2 `src/ayasee/codescanning.py` and `GitHub.code_scanning_probe`

## 2. Workflows

- [x] 2.1 Failing workflow tests for the `code-scanning` input of `iac-pr` and `iac-nightly`
- [x] 2.2 `iac-pr`: input, `upload-sarif` skipped, artifact retention, summary step
- [x] 2.3 `iac-nightly`: input, uploads and `findings new` skipped, artifact, summary, notify condition

## 3. init and doctor

- [x] 3.1 Failing tests: init writes `code-scanning: false` from the detection and the flags; `--upgrade` refuses the flag
- [x] 3.2 `scaffold.py`: `choose_code_scanning`, rendering the callers, `--code-scanning`
- [x] 3.3 Failing tests: doctor warns on an uploading caller without code scanning, unknown never ok
- [x] 3.4 `doctor.py`: `check_code_scanning`

## 4. Docs and release notes

- [x] 4.1 docs/concepts/pr-checks.md, docs/concepts/drift.md, docs/guides/adopt.md (plan table), CHANGELOG `[Unreleased]`

## 5. Close

- [x] 5.1 Run every check of CONTRIBUTING; validate and archive the change
