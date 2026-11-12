# DORA RTS 2024/1774, Article 17: control mapping

This page maps the points of Article 17 of Commission Delegated Regulation (EU) 2024/1774 that
concern changes to production to the controls `ayasee` provides. Source:
[EUR-Lex, Delegated Regulation (EU) 2024/1774](https://eur-lex.europa.eu/eli/reg_del/2024/1774/oj).

> **Disclaimer.** This is a technical mapping, not legal advice and not a statement of compliance.
> Compliance depends on how a financial entity configures and operates the framework and on its own
> policies. Adopters remain responsible for their assessment. The point labels follow the published
> text of [Article 17](https://www.springlex.eu/en/packages/dora/rts-rmf-regulation/article-17/)
> (Springlex rendering of the OJ text); the authoritative text is the one on EUR-Lex.

## Mapping

| Art. 17 point | Topic | Framework control |
|---|---|---|
| (1)(a) | Security verification of the change | The Rego policy pack on every plan and the scanners (Trivy and Checkov on the pull request and the nightly re-scan, Trivy on the prod plan). Break-glass keeps the policy pack (stage `prod`, deny rules fail the run); only the Trivy plan scan is not run there. |
| (1)(b) | Independence of the person approving a change | The `prod` environment has required reviewers, `prevent_self_review = true` (the person who triggers a run cannot approve it) and no admin bypass (D2). `prod-plan` and `prod-in-sync` hold no reviewers, so reviewers approve once, on the apply, after the plan has been produced and scanned. |
| (1)(c) | Roles and testing | Roles: one GitHub environment and one Azure identity per stage, with separate reviewer sets for `prod` and `prod-breakglass` (see [the roles table](../../modules/azure-stage-identity/README.md#role-assignments-not-created-here)). Testing: the `dev` and `uat` stages apply and converge-check each commit before the prod plan, and the guard stops a later stage while an earlier stage is red. |
| (1)(d) | Documentation and recording | The ledger record per deployment (SHA, run, per-unit result, approvers, break-glass incident), the approvals API (the approvers are read from GitHub for the run's environment, never typed in) and the GitHub deployment mirror. |
| (1)(e) | Fall-back and rollback | Revert the commit on `main`; the revert goes through the same staged run. The ledger shows the last SHA that converged in each environment. Break-glass refuses a commit older than the one live in prod unless `allow-rollback` is `true`. |
| (1)(f) | Safeguards for emergency changes | Break-glass (`iac-breakglass.yml`, D3): a required incident reference, the separate `prod-breakglass` environment with its own reviewers and identity, `main`-only commits, the policy pack, and the whole prod tree planned and applied (R71). |
| (1)(g) | Approval of emergency changes after the fact | An issue labelled `ayasee:post-approval` is opened on every break-glass use, even when the apply fails, with a checklist that includes review by an independent approver. |
| (1)(h) | Security impact of the change | The risk tiers at the prod gate: critical and high changes are counted and listed, a grants or permissions update that removes principals is tier high with the lost principals listed, and `forget` of a critical type counts as critical. |

## Solo mode is non-compliant

`solo_mode: true` in `.ayasee.yaml` is an opt-in for a single maintainer. It removes independent
approval, so it does not satisfy point (1)(b). Every load of the configuration prints:

```
NON-COMPLIANT: solo_mode disables independent approval (DORA RTS 2024/1774 Art. 17(1)(b))
```

What it keeps and what it drops, for a single maintainer:

- **Kept:** every prod apply still waits for an approval in the `prod` environment, break-glass
  still waits in `prod-breakglass` and opens its post-approval issue, plan binding and the risk
  summary are unchanged, and the ledger records who approved. The approval is deliberate, but it is
  the author's own.
- **Dropped:** the second person. The `github-environments` module refuses the same person as
  prod and break-glass reviewer and sets `prevent_self_review`; its `solo_mode = true` lifts both on
  `prod`, `prod-breakglass` and `prod-pr-plan` (reviewers stay required, admins still cannot bypass).
- **Labelled:** the line above appears in the log and summary of every run, and the cockpit tags
  the repository `solo mode · non-compliant`.

The e2e scenario `solo` checks this path end to end. Do not use it for regulated workloads.

## Break-glass in practice

Start `iac-breakglass` with `workflow_dispatch` (or call it with `workflow_call`) from `main` (a
dispatch from another ref fails). Inputs:

- `incident`: `ABC-123` or `INC123`. Anything else is refused.
- `sha`: a full 40-character commit that is an ancestor of `origin/main`. Unmerged code is refused.
- `allow-rollback`: `true` or `false` (default `false`). The live prod SHA is read from the ledger. If
  `sha` is older than it (the live SHA is not an ancestor of `sha`), the run is refused unless this is
  `true`. When allowed, the run logs `::warning::rollback ...` and the post-approval issue gets a
  "Rollback from `<live sha, 12 characters>`" line. With no prod ledger record (first deploy) there is
  no check.

There is no unit scope (R71): break-glass always plans and applies the whole prod tree. Unchanged
units are no-ops, so the ledger record of prod stays truthful for every unit.

The run, in order: validate the inputs, wait for the `prod-breakglass` reviewers, check the ancestry
and the rollback rule, read the approvers (it fails when there are none), plan, run the policy pack
(stage `prod`), apply in the same job (`binding-mode: breakglass`, prod only, with the same plan
encryption and state lock timeout as the normal prod stage), write the ledger record with the incident
and approvers (also when the run is cancelled after the apply started), open the stop-the-line issue
if the apply failed, open the post-approval issue and notify.

The `prod-breakglass` reviewers approve before the plan exists, so they cannot review what will change.
The deny rules of the policy pack are the machine check standing in for that missing review, and the
post-approval issue (with the change counts) is the review after the fact. Trivy is not run on this
path.

Stop-the-line is not enforced on this path (no guard step): an emergency fix has to be able to ship
while a stage is red. A failed apply does open the `ayasee:stop-the-line` issue for prod, with the
`ayasee:apply-failed` label, so later stages and nightly drift stop until it is looked at.

The post-approval issue is titled `post-approval: break-glass <incident>`, carries the labels
`ayasee:post-approval` and `env:prod`, and lists the incident, the first 12 characters of the SHA,
the run link, the approvers and the change counts (never resource values). Its checklist must be
completed after the fact:

- [ ] Change reviewed by an independent approver
- [ ] Incident record linked
- [ ] Follow-up PR merged to main
- [ ] Drift check clean

Each use opens a new issue. Close it only when every box is ticked.

## Setting up the `prod-breakglass` environment

1. Create the environment `prod-breakglass` in the repository settings.
2. **Reviewers**: a set of people distinct from the `prod` reviewers (an on-call or incident-manager
   group), with **prevent self-review** on, and **allow administrators to bypass** off.
3. **Deployment branches**: selected branches, `main` only. The workflow also refuses any other ref.
4. **Identity**: its own Azure identity with prod apply rights, Storage Blob Data Contributor on the
   prod state container, `wrapKey` and `unwrapKey` on the plan key, and read and write on the ledger
   (full table: [azure-stage-identity](../../modules/azure-stage-identity/README.md#role-assignments-not-created-here)). The federated credential subject must
   be `repo:<owner>/<repo>:environment:prod-breakglass`. The `prod` and `prod-breakglass`
   environments never share an identity: if they did, anyone able to start a run in the more weakly
   reviewed environment would hold the other's rights.
5. **Variables** (variables, not secrets: OIDC): `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID` and `AYASEE_KEY_VAULT_KEY_ID` with the prod values (the key must
   encrypt and decrypt prod plans), and **`AYASEE_LEDGER_URL`, which is required** here: the
   workflow fails when it is unset.
6. Create the labels `ayasee:post-approval` and `env:prod` if the repository requires labels to
   exist (GitHub creates missing labels when an issue is opened with them).
7. Restrict who can start the workflow (for example with repository rules or a protected caller
   workflow), and monitor the label `ayasee:post-approval` for open issues.

## What the binding mode is not

`binding-mode: breakglass` is a consistency check in the actions (prod only, valid incident
reference). It is not an authorisation boundary: anyone who can run arbitrary steps with the prod
identity can bypass it. The boundaries are the `prod-breakglass` environment (reviewers, `main`-only
branches) and the identity's federated credential subject.
