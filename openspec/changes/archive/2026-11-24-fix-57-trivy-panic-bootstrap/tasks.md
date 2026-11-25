## 1. Reproduce

- [x] 1.1 Install Trivy 0.70.0 and 0.76.2 checksum-verified in a scratch directory; run `trivy config` repeatedly on the bootstrap of `ayasee init --profile cloudflare-s3 --plan free` with the module source pointed at this checkout; record the exit codes

## 2. Module

- [x] 2.1 Add a tofu test for the workflow gate on Free with unset reviewers (no reviewer anywhere, the environment set unchanged)
- [x] 2.2 Represent "no reviewers" as an empty set in `modules/github-environments` (`local.reviewed`), with a precondition that the environment gate's `prod` and `prod-breakglass` carry reviewers
- [x] 2.3 Add a tofu test that the environment gate refuses unset reviewers on prod and break-glass; rerun the Trivy repro after the change and record the exit codes

## 3. Scanner crash

- [x] 3.1 Failing tests first: `sarif-scrub` on a missing input fails with a "wrote no report" message; the PR, nightly and prod-plan steps after Trivy check the report exists
- [x] 3.2 `sarif-scrub` reports a missing input as a scanner that wrote no report
- [x] 3.3 The scrub steps of `iac-pr` and `iac-nightly` and the prod-plan findings list fail with an `::error::` naming the scanner when the report is missing; state `version: v0.70.0` on each `trivy-action` step

## 4. Regression test in CI

- [x] 4.1 Add Trivy (pinned, SHA-256) to `scripts/install-dev-tools.sh`, and a unit test that it matches the workflows' `version:`
- [x] 4.2 Integration test: `trivy config` several times on what `ayasee init` writes (both profiles, both gates), module sources rewritten to this checkout; it fails on the unfixed module
- [x] 4.3 Install Trivy in CI's `integration` job

## 5. Docs and release notes

- [x] 5.1 Update `docs/concepts/pr-checks.md`, `CONTRIBUTING.md` (dev tools) and the module README if needed
- [x] 5.2 `CHANGELOG.md` under `[Unreleased]`

## 6. Close

- [x] 6.1 Run every check of CONTRIBUTING; validate and archive the change
