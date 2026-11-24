## Why

In an adopter's repository the `checks` job of `iac-pr` fails on most runs (#57): Trivy crashes
(`panic: can't use ElementIterator on null value`) while it evaluates the `github-environments`
module that the bootstrap written by `ayasee init` calls. With `gate_mechanism = "workflow"` and the
reviewer variables left unset, the module's `for_each` local `environments` holds null objects, and
Trivy's for_each deferral (`localHasDynamicValues`) calls `AsValueMap` on whichever element Go's map
order visits first: a null object panics, an untyped null does not, so the crash is random. Trivy
0.70.0 (the version ayasee's workflows use) and 0.76.2 (the latest release) both panic. The crash
then surfaces as an opaque `FileNotFoundError` in `ayasee sarif-scrub`, and ayasee's own CI never
saw it because the examples call the module by relative path with reviewers set.

## What Changes

- `modules/github-environments`: "no reviewers" is an empty reviewer set, never null, so no
  `for_each` local holds a null object. Reviewers are required where the set is not empty; every
  refusal, validation and postcondition keeps its meaning, and a new precondition states that the
  environment gate's `prod`, `prod-breakglass` and `prod-pr-plan` always carry reviewers.
- `ayasee sarif-scrub`: a missing input file fails with a message saying the scanner wrote no
  report (it crashed or failed before writing), not a bare `FileNotFoundError`.
- `iac-pr`, `iac-nightly` (rescan) and `iac-staged` (prod-plan): the step after each Trivy run
  fails with an `::error::` naming a scanner crash when Trivy wrote no report. In `iac-pr` both
  scanners still run after an earlier failure. The Trivy version (`v0.70.0`, the action's default
  until now) is stated on each `trivy-action` step.
- `scripts/install-dev-tools.sh` installs the same Trivy version, checksum-verified, and a new
  integration test runs `trivy config` several times on what `ayasee init` writes for each profile
  and gate (module sources pointed at this checkout), in CI's `integration` job.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-bootstrap`: the module represents "no reviewers" as an empty set and refuses an
  environment gate whose prod environments would have none; what `ayasee init` writes must pass
  Trivy.
- `security-scanning`: a scanner that writes no report fails the job as a scanner failure, with a
  clear message.
- `pr-checks`: the static-checks requirement names the scanner-crash case.

## Impact

- `modules/github-environments/main.tf` and its tests; no change to the resources planned.
- `src/ayasee/sarif.py`; `.github/workflows/iac-pr.yml`, `iac-nightly.yml`, `iac-staged.yml`;
  `.github/workflows/ci.yml`; `scripts/install-dev-tools.sh`; new test under `tests/`.
- Docs: `docs/concepts/pr-checks.md`, `CONTRIBUTING.md`; `CHANGELOG.md`.
