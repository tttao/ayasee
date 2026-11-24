## Context

Trivy's Terraform evaluator (`pkg/iac/scanners/terraform/parser/evaluator.go`, unchanged from
0.70.0 to 0.76.2) defers a `for_each` that references a local when `localHasDynamicValues` finds a
`DynamicPseudoType` element. It walks the local's elements in Go map order and calls `AsValueMap` on
every object-typed element. In `local.environments` of `github-environments`, the workflow gate put
`null` (untyped) on most environments and `var.prod_reviewers` / `var.breakglass_reviewers` (null,
but typed `object({teams, users})`) on `prod` and `prod-breakglass`. If an untyped null is visited
first the function returns early; if a typed null object is visited first, `AsValueMap` panics.
Hence the random exit 2.

## Goals / Non-Goals

**Goals:** what `ayasee init` writes scans cleanly; a scanner crash fails the job with a message
that says so; a regression test in CI.

**Non-Goals:** changing which environments, reviewers or branch policies the module plans; fixing
Trivy upstream; changing which Trivy findings fail a run.

## Decisions

- **Empty reviewer set instead of null.** Every element of `local.environments` becomes a non-null
  `{teams, users}` object; "reviewers required" is `length(teams) + length(users) > 0`
  (`local.reviewed`). The variable validations already make that set non-empty on `prod` and
  `prod-breakglass` under the environment gate and null under the workflow gate, so the planned
  resources are identical. A new precondition refuses the environment gate with an empty set on
  `prod`, `prod-breakglass` or `prod-pr-plan`, so a future edit cannot turn "empty" into "ungated".
  *Rejected:* wrapping each element (`{ reviewers = ... }`): it keeps a null object one level
  down, and leaves Trivy deferring the expansion; `coalesce` on the variables alone: still
  needs the same empty-set semantics, with less explicit code.
- **No Trivy bump.** 0.76.2, the newest release, panics on the same input (10 of 10 runs). The
  version is stated explicitly (`version: v0.70.0`, the action's default) on each `trivy-action`
  step so that the regression test runs the version callers run; `scripts/install-dev-tools.sh`
  pins the same version with its SHA-256, and a unit test keeps the two equal.
- **Crash detection where the report is read.** The scrub step (PR, nightly) and the findings list
  (prod-plan) check that the report exists and fail with an `::error::` that names the scanner;
  `sarif-scrub` itself reports a missing input the same way for anyone calling it directly. The
  Trivy step's own exit code still fails the job; these only make the cause readable.
- **Regression test as an integration test.** It needs the Trivy binary, like the other
  integration tests need tofu and terragrunt; CI's `integration` job installs it. Module sources
  `git::…//modules/<m>?ref=<sha>` are rewritten to a relative path into a copy of this checkout's
  `modules/`, the closest offline equivalent of the download (the panic reproduces through the
  relative path: 9 of 10 runs before the fix).

## Risks / Trade-offs

- Stating `version:` means a Renovate bump of `trivy-action` no longer moves Trivy by itself → the
  install script, the workflows and the test move together, by hand, in one PR.
- Trivy may change its evaluator again → the regression test runs on every PR.
