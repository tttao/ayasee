## Context

`iac-pr` publishes the scrubbed Trivy and Checkov SARIF from a separate `upload-sarif` job that
holds `security-events: write` and runs no PR code (R39); `iac-nightly`'s `rescan` uploads the
SARIF of `main` and then reads the code-scanning API (`ayasee findings new`). Code scanning is
free on public repositories; a private (or internal) repository needs GitHub Code Security, sold
on GitHub Team and Enterprise only. Without it both uploads fail and the nightly count fails on
its API error.

## Goals / Non-Goals

**Goals:** green pull requests on a private repository without Code Security, with the scanners
still gating `checks`; an explicit, visible state ("code scanning is off") rather than a silent
one; `init` and `doctor` that set and check it.

**Non-Goals:** a replacement for the Security tab (alert history, dismissal); a "new findings"
count without code scanning.

## Decisions

### An explicit caller input, not detection inside the workflow

`code-scanning: true|false` on `iac-pr` and `iac-nightly`, default `true`.

Rejected: letting `upload-sarif` catch the "not enabled" error and pass. The upload step would
need `continue-on-error` and a parse of the action's failure, which also swallows real
misconfiguration (a token without `security-events`, a broken SARIF, an outage) under the same
green; and the job holding `security-events: write` would grow logic. Rejected too: a probe job
before the upload (an extra API read with a privileged token on every PR, and a verdict that can
flip with an outage). An input is fixed in the caller, reviewed like any other change, and its
off state is printed on every run.

Default `true` keeps every existing caller unchanged; a repository that needs `false` is red until
the caller says so, which `doctor` explains — loud rather than silently dropping findings.

### Off means no upload job at all, and the SARIF stays as an artifact

With `false`, `upload-sarif` is skipped by its job `if:` (no step with `security-events: write`
runs), `ayasee-sarif` is kept 30 days instead of one (the only record of the findings), and the
`checks` job writes a summary with per-scanner counts (no values, D10). In `rescan`, the upload
steps, `ayasee findings new` and the `new_findings` notify are skipped (the notify condition now
requires the input, because a skipped count is `''`, which would read as "not zero"), the SARIF is
kept as `ayasee-rescan-sarif`, and a notice plus summary say no `new_findings` notification is sent.

Job permissions cannot depend on an input, and a called workflow's job may not ask for more than
the caller grants even when it would be skipped, so callers keep granting `security-events: write`
and `rescan` keeps it. That is acceptable: `upload-sarif` does not run, and `rescan` runs only
`main` (trusted, as before).

### init: detect, and decide conservatively

`src/ayasee/codescanning.py` returns AVAILABLE, UNAVAILABLE or UNKNOWN, failing closed:
AVAILABLE only on evidence (public; `security_and_analysis` shows `code_security` or
`advanced_security` enabled; or the alerts API answers, including 404 "no analysis found"),
UNAVAILABLE only on GitHub's own word (`security_and_analysis` all disabled; 403 "Code scanning is
not enabled" / "... must be enabled"), UNKNOWN otherwise (any other error, unreadable repository).

`init` writes `code-scanning: false` for UNAVAILABLE, and for UNKNOWN on a private repository whose
plan is Free, Pro or Team (Free and Pro cannot buy Code Security for private repositories; on Team
it is a paid add-on most do not have, which issue #55 asks to assume). Otherwise it keeps the
upload. Every choice that is not plain evidence prints a note with the flag that reverses it.

### doctor: warn, not fail

A red `upload-sarif` is not a required check and does not weaken a gate; the scanners still gate
`checks`. So a definite UNAVAILABLE and an UNKNOWN both warn (different texts), never ok. Skipped
without a token, as the other GitHub checks are.

## Risks / Trade-offs

- [The error texts GitHub uses may change] → an unrecognised refusal is UNKNOWN, which warns; it
  never reads as available.
- [A Team repository with Code Security but a token that cannot see it gets `false` from init] →
  init's note names `--code-scanning`; doctor with an admin token sees `security_and_analysis`.
- [With `false` nightly sends no `new_findings`] → said in the run summary and the docs; PR checks
  still fail on high and critical findings.
