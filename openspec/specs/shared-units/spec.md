# shared-units Specification

## Purpose

`shared_units` are prod units that exist once and serve every stage (a tunnel, an access
application, a DNS zone). They live under `<units_root>/prod` only, reach production only through the
prod gate, are flagged in the gate summary, and get a read-only prod plan in pull requests: in
`iac-pr` with the environment gate, or in `iac-pr-shared` started by a prod reviewer with the
workflow gate. Code: `src/ayasee/prshared.py`, `src/ayasee/config.py` (`check-shared`). Reference:
`docs/concepts/staged-run.md#units-that-serve-every-stage`, `docs/concepts/pr-checks.md#shared-units`.

## Requirements

### Requirement: Shared units exist only under prod
`ayasee config check-shared` SHALL exit 1 with one line per problem when a listed unit (named as the path below `<units_root>/prod` with `/` written `__`) is missing under `prod` or also present under another stage; `resolve` of `iac-staged` and the PR `checks` job SHALL run it.

#### Scenario: Shared unit also in test
- **WHEN** `edge__tunnel` is listed and `stages/test/edge/tunnel` exists
- **THEN** `check-shared` fails the run

### Requirement: Changed shared units of a PR
`ayasee changed-units --env prod --shared a,b` SHALL print the shared units a PR changes: the unit's name for a change under it; every shared unit for a change inside `iac_root` but outside `units_root`, a file under `<units_root>/prod/` that belongs to no unit, the configuration file, or a diff that cannot be computed; nothing for changes to other stages or non-shared prod units.

#### Scenario: root.hcl change
- **WHEN** a PR changes `root.hcl`
- **THEN** every shared unit is reported as changed

### Requirement: PR plan of shared units with the environment gate
With `gate_mechanism: environment`, when a PR changes shared units, `plan-shared` SHALL run in `prod-pr-plan` (which carries the prod reviewers), plan those units against prod read-only (`binding-mode: replan`, `encrypt: false`, `lock: false`, plan files deleted, never applied), run the pack with stage `prod`, and post a second sticky comment (`pr-plan-shared` marker) headed "Shared prod units plan" so the first-stage comment never waits.

#### Scenario: Reviewer gate on PR code
- **WHEN** a PR changes a shared unit
- **THEN** `plan-shared` waits for a prod reviewer before the PR code receives the prod read-only identity

### Requirement: No in-PR shared plan with the workflow gate
With `gate_mechanism: workflow`, read at both the PR and its base commit, `iac-pr` SHALL NOT plan shared units; `shared-changes` SHALL warn and output `pending`, and the shared comment SHALL give the PR number and head SHA a prod reviewer authorises with `iac-pr-shared`.

#### Scenario: PR switches its own mode
- **WHEN** a PR changes `gate_mechanism` from `workflow` to `environment`
- **THEN** `plan-shared` still does not run, because the base commit has the workflow gate

### Requirement: iac-pr-shared authorisation
`iac-pr-shared` SHALL run its `verify` job with no environment and no secret and SHALL refuse unless the run is a `workflow_dispatch` on `refs/heads/main` with `github.actor` equal to `github.triggering_actor`; `main`'s configuration has `gate_mechanism: workflow` and `shared_units` and lists the actor in `reviewers.prod`; the actor has write access; the PR is open, from this repository, into `main`, with head exactly `sha`; the actor did not open it nor author or commit any of its commits (unless `solo_mode`; 250 commits or more refused); and `prod-pr-plan` deploys from `main` only.

#### Scenario: Fork PR
- **WHEN** a reviewer authorises a PR whose head repository is a fork
- **THEN** `verify` refuses

#### Scenario: PR moved on
- **WHEN** the PR head is no longer `sha` at verification
- **THEN** `verify` refuses and a new run is needed

### Requirement: iac-pr-shared plan and comment
The `plan` job SHALL run in `prod-pr-plan` (named by `verify`), refuse an attempt by anyone but the accepted reviewer, check out exactly `sha` with `persist-credentials: false`, and plan the changed shared units read-only as `plan-shared` does; the `comment` job SHALL check out `main`, post the `pr-plan-shared` comment naming the commit planned and the reviewer, and say when the PR has moved on since.

#### Scenario: Push after authorisation
- **WHEN** a commit is pushed to the PR after the reviewer started `iac-pr-shared`
- **THEN** the comment says the PR moved on and the new head needs a new run

### Requirement: Not available on GitHub Free
On a private repository on GitHub Free, `iac-pr-shared` SHALL refuse to run, the bootstrap SHALL refuse `shared_units_pr_plan`, and `ayasee doctor` SHALL fail on its caller; shared units are then first seen at the prod approval.

#### Scenario: Free plan
- **WHEN** `iac-pr-shared` is started in a private Free repository
- **THEN** it refuses

### Requirement: Shared changes go through the prod gate only
Shared units SHALL be planned by `prod-plan` and applied only by the gated prod path; no pre-prod stage SHALL plan or apply them.

#### Scenario: Test hostname in a shared tunnel
- **WHEN** a change to a shared tunnel only adds a test hostname
- **THEN** it reaches the tunnel only after the prod approval
