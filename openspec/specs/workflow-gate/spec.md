# workflow-gate Specification

## Purpose

`gate_mechanism: workflow` (decision D15, issue #40) lets a private repository below GitHub
Enterprise keep an independent prod gate that ayasee enforces instead of GitHub: the staged run
stops after `prod-plan` with an approval issue, and a listed reviewer applies exactly that plan with
`iac-approve` (`.github/workflows/iac-approve.yml`). Code: `src/ayasee/approve.py`. Reference:
`docs/governance/decisions.md#d15-the-workflow-gate`, `docs/concepts/staged-run.md#the-workflow-gate`.

## Requirements

### Requirement: Staged run stops at an approval issue
With `gate_mechanism: workflow`, when the prod plan has changes or `gate-changed` is true, `iac-staged` SHALL replace the `prod` job with `prod-approval` (no environment, no secret, `contents: read`, `issues: write`), which SHALL open or rewrite the single open `ayasee:prod-approval` issue with the commit SHA, the SHA-256 of `manifest.json` (`manifest-digest`), the change counts and the run link, and never a plan value.

#### Scenario: Newer plan
- **WHEN** a second commit reaches `prod-plan` while an approval issue is open
- **THEN** the same issue is rewritten for the newer plan

### Requirement: Verify job checks the approver before any secret
The `verify` job of `iac-approve` SHALL have no environment and receive no secret, and SHALL refuse unless: the run is a `workflow_dispatch` on `refs/heads/main` with `github.actor` equal to `github.triggering_actor`; the configuration of `main` has `gate_mechanism: workflow` and lists the actor in `reviewers.prod` (case ignored); the actor has write access; the commit is in the history of `main`; the newest unexpired `ayasee-prod-plan` artifact of that commit comes from a successful run on `main` that calls `iac-staged.yml` and its manifest has exactly the given digest and binds `prod` at that commit; the actor did not push, author, commit, open or merge the change unless `solo_mode`; and the `prod` environment deploys from `main` only, or the plan has no branch policies at all (printed as a DEVIATION).

#### Scenario: Re-run by someone else
- **WHEN** a different person re-runs a failed `iac-approve` attempt
- **THEN** `verify` refuses because `github.actor` differs from `github.triggering_actor`

#### Scenario: Author approves own change
- **WHEN** the author of the commit starts `iac-approve` without `solo_mode`
- **THEN** `verify` refuses

#### Scenario: Wrong digest
- **WHEN** the given `manifest-digest` does not match the newest plan artifact of the commit
- **THEN** `verify` refuses

### Requirement: Reviewer trust anchored at the last approved apply
The `apply` job SHALL run `ayasee approve baseline` against the ledger and SHALL refuse unless the commit is not already live in prod, the actor was in `reviewers.prod` at the last approved commit (the newest prod record with an approver, never an in-sync record), and, unless `solo_mode` is on at both commits, no commit since the last approved one has the actor as author or committer. Before the first approved apply, or when it predates the mode, `main`'s list alone SHALL decide and the run SHALL print `DEVIATION: bootstrap trust: ...`.

#### Scenario: Reviewer added by the change
- **WHEN** a commit adds a login to `reviewers.prod` and that login starts `iac-approve`
- **THEN** the apply is refused because the login was not listed at the anchor

### Requirement: Gate-setting changes always need approval
With the workflow gate on either side (the planned commit or the last approved commit), a change of `gate_mechanism`, `reviewers.prod`, `reviewers.breakglass` or `solo_mode` against the last approved commit SHALL set `gate-changed` and SHALL never be promoted in sync, even with an empty plan; the approval SHALL become the new anchor. A change of the environment gate alone (both sides `environment`) SHALL keep the in-sync path.

#### Scenario: Empty plan that adds a reviewer
- **WHEN** a commit only edits `reviewers.prod` and the prod plan is empty
- **THEN** the run goes to `prod-approval` instead of `prod-in-sync`

### Requirement: Apply exactly the approved plan
The `apply` job SHALL run in the `prod` environment and its concurrency group, only after `verify` succeeded and only on `main`; it SHALL check the commit is on `main`, run the guard, check the digest again, apply with `binding-mode: encrypted_plan`, write the ledger with the actor as approver, close the approval issue and notify. No step before the apply step SHALL receive a provider or plan-key secret.

#### Scenario: Superseded while waiting
- **WHEN** a newer commit converged in the stage before prod after the approval issue was opened
- **THEN** the guard refuses the apply as `superseded`

### Requirement: Labelled deviation
Every run and the cockpit SHALL label `gate_mechanism: workflow` as `DEVIATION: gate enforced by ayasee, not by GitHub environment protection`.

#### Scenario: Cockpit tag
- **WHEN** the cockpit collects a repository whose `main` sets `gate_mechanism: workflow`
- **THEN** the repository row carries a deviation label for it
