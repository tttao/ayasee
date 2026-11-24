## Context

`import-plan` needs a plan of the unit (state read, provider refresh) and the provider token for
read-only API lookups. Who may run that from GitHub, with which credentials, is the whole design.

## Decisions

### Only the first stage, in `<first stage>-plan`

`<first stage>-plan` already accepts every branch and holds read-only credentials: `iac-pr` runs
a pull request's code with them, and anyone with write access can push a branch whose own
workflow names that environment. A `workflow_dispatch` of `iac-import` on a branch (write access
needed) runs the branch's code with exactly those credentials, so it reaches nothing a pull
request's plan does not.

Rejected: other stages from the branch. `prod-plan` and the other stage environments are
main-only and must stay so; uat-like stages plan and apply with one write-capable identity.
Rejected for now: a reviewer-dispatched run on `main` for a branch SHA in a main-only environment,
the pattern of `iac-pr-shared`. It would need its own verify job (a prod reviewer who is not the
author, head SHA pinned), a main-only read-only environment per stage (with the environment gate
`prod-pr-plan` accepts PR branches behind reviewers; with the workflow gate it is main-only; on
GitHub Free it does not exist), and it would hand prod read credentials to unreviewed code at a
person's request for a step that runs once per unit. It is left as a follow-up. Other stages are refused with a
message that points to the local CLI.

### GitHub Free

`<first stage>-plan` there holds no secret of its own: the job reads the repository secrets,
which every stage and the pull-request plan already read (D15 table). `iac-import` adds no reader.
Documented, not refused: refusing would not remove the exposure, which `iac-pr` has already.

### Job split (R39)

`resolve` (contents: read; no environment) checks out the dispatched commit and validates the
inputs with `ayasee import-target` (stage = first stage, unit a plain relative path whose
`terragrunt.hcl` exists under `<units_root>/<stage>`). `import` (contents: read, id-token: write;
environment from `resolve`) plans with the plan action (`binding-mode: same-job`,
`encrypt: false`, `lock: false`), runs `import-plan --plan-dir`, then always deletes
`$RUNNER_TEMP/ayasee` (plans, plan JSON, engine logs). `comment` (contents: read,
pull-requests: write) checks out the default branch, never the dispatched one, and posts the
summary on the open pull request whose head is the dispatched branch in this repository.

### Credentials per step

The plan step gets the provider tokens and `AYASEE_PROVIDER_ENV`, as in `iac-pr`. The
`import-plan` step gets `CLOUDFLARE_API_TOKEN` only (the lookups read nothing else).

### What leaves the job

The artifact `ayasee-imports` holds `imports.tf` files (addresses and provider IDs, IDs checked as
plain identifiers) and the summary (outcome per address). The plan JSON never leaves: the
`import-plan` output and the summary are built from addresses and IDs, and a test injects a
sentinel value in the plan and checks it is absent.

## Risks

- The comment is advisory: the branch's code produced the plan.
- A branch can change its own copy of the caller and of the reusable workflow pin: the boundary
  is the environment, not the workflow file, as for `iac-pr`.
