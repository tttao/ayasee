# Design decisions D1 to D15

The docs and the code cite these decisions by number, so that a behaviour can be traced to its
reason. Each row gives the question, the options that were considered and the default ayasee
takes. A change to a safety default starts here: change the decision first, in an issue that states
the reason and the trade-off (see [governance](../../GOVERNANCE.md#how-decisions-are-made)).

| # | Decision | Options considered | Default |
|---|---|---|---|
| D1 | Prod plan binding | (a) encrypted saved plan and hash; (b) re-plan with strict equality, including a hash of the non-sensitive `after` values; (c) re-plan and accept a subset | **(a)**, with (b) as the fallback where encryption keys are not available; (c) is dropped |
| D2 | Approval independence | Self-approval allowed or prevented | **Prevented**, admin bypass disallowed; `solo_mode: true` is an opt-in, labelled non-compliant for DORA, SOX and ISO |
| D3 | Break-glass path | Admin bypass; a dedicated `prod-breakglass` environment with its own reviewers and an incident reference; none | **Dedicated environment**, which always opens a post-implementation approval issue (DORA RTS Art. 17(1)(g)) |
| D4 | Concurrency | `queue: single` (newest wins) or `queue: max` (first in, first out) | **single**, with whole-tree applies and rejection of a stale SHA; `max` only after GHES parity is verified |
| D5 | Source of truth for the live SHA | Deployments API or a backend ledger | **Backend ledger** written by the stage identity; GitHub deployments are a projection of it |
| D6 | Engines | OpenTofu only; OpenTofu and Terraform; plus Terragrunt | **OpenTofu and Terragrunt first** (plan encryption needs OpenTofu); plain Terraform as a secondary profile using D1(b) |
| D7 | Identity model | Entra user-assigned managed identity per stage; Databricks service principal federation; both | **Entra identity per stage** (covers azurerm, the backend and Databricks); Databricks federation as an option; subjects are validated at onboarding |
| D8 | Cockpit hosting | Private Pages (GHEC); GHES Pages; Azure Static Web Apps with Entra sign-in; rendered into issues or summaries | **Pluggable renderer on one snapshot schema**; private Pages on GHEC; sanitized data only on GHES |
| D9 | Prod gate | Always, or only for risky changes | **Always** until a repository has two clean weeks, then risky-only per repository |
| D10 | Scanners | Trivy and Checkov only; plus a Databricks Rego pack on the plan JSON | **Both**, with the Databricks pack inside the job; plan JSON and values are never uploaded, in SARIF or elsewhere |
| D11 | Platform target | GHES first or github.com first | **github.com first**, with a GHES compatibility suite |
| D12 | Build or contribute | A new framework, or modules on tfaction or Atmos | **A new, thin framework** for staged promotion, the cockpit and the risk pack, with an explicit interop story |
| D13 | Boundary with Databricks bundles | Ignore; an ownership contract; cockpit integration | **Ownership contract now**, cockpit integration later |
| D14 | Licence, name and provenance | Apache-2.0 or MIT; a neutral name | **Apache-2.0**, the name `ayasee`, built from public sources only: see [provenance and licence](PROVENANCE.md) |
| D15 | Who enforces the prod gate | (a) GitHub, with required reviewers on the `prod` environment; (b) ayasee, with a second run that a listed reviewer starts and that ayasee checks; (c) refuse repositories whose plan has no environment reviewers | **(a)**; (b) is the opt-in `gate_mechanism: workflow`, labelled a DEVIATION, for a private repository below GitHub Enterprise (issue #40); (c) stays the answer of the default gate. See [D15 in detail](#d15-the-workflow-gate) |

## D15: the workflow gate

GitHub enforces required reviewers on an environment only in a public repository or on GitHub
Enterprise. `gate_mechanism: workflow` lets a private repository on Free, Pro or Team still have a
prod gate, enforced by ayasee instead of GitHub:

- The staged run stops after `prod-plan`. It keeps the encrypted plan and its manifest as an
  artifact of the run, as today, and opens (or rewrites) one issue labelled
  `ayasee:prod-approval` with the commit SHA, the SHA-256 of the manifest and the change counts.
- A reviewer starts `iac-approve` on `main` with that SHA and digest. Before any secret is used,
  ayasee checks that the run is a `workflow_dispatch` on `refs/heads/main`, started and not
  re-run by one person (`github.actor` equal to `github.triggering_actor`); that this person is in
  `reviewers.prod` of `.ayasee.yaml` **read at `main`**, and was already there at the commit live
  in prod (so a change cannot add its own approver); that they have write access; that the
  commit is on `main` and its newest unexpired plan comes from a successful `iac-staged` run on
  `main` whose manifest has exactly that digest; that they did not push, write or merge the change,
  nor write any commit since the one live in prod (unless `solo_mode`, at both commits); and the
  guard (the commit is the one live in the stage before prod; no stop-the-line issue).
- It then applies exactly the approved encrypted plan and records the approver in the ledger.

**What it keeps.** D1: the applied plan is the approved plan, bound by the manifest digest and
the plan binding, and a stale plan still stops the apply. D2: independence, checked by ayasee
from the run's actor (stronger than GitHub's `prevent_self_review`, which only excludes whoever
started the run). D4 and D5: the guard and the ledger, which now records the approver of the
`iac-approve` run.

**What it gives up.** Nothing in GitHub waits: the gate is ayasee's own code, and a change to the
callers on `main` can remove it. How much else holds depends on the plan:

| Repository | Environment reviewers | Main-only environments and environment secrets | Rulesets on `main` | What `gate_mechanism: workflow` guarantees |
|---|---|---|---|---|
| Public, or private on GitHub Enterprise | yes | yes | yes | Everything below, but the default gate is stronger: use `environment` |
| Private on GitHub Pro or Team | no | yes | yes | The prod secrets reach only jobs on `main` in the prod environment; reaching the apply needs a listed reviewer, checked as above; changing the reviewers or the gate needs a pull request on `main`, which a ruleset can require a review for |
| Private on GitHub Free | no | **no** | **no** | Every apply waits for a listed reviewer and applies only the approved plan, but the prod secrets are repository secrets that any collaborator with write access can read from a workflow of any branch, every stage reads the same secrets, and anyone with write access can push to `main`. It stops mistakes and unreviewed applies, not a collaborator who means harm: keep the collaborators with write access to the reviewers |

Break-glass (D3) and the pull-request plan of shared units have no workflow-gate form yet: in this
mode `iac-breakglass` refuses to run and `iac-pr` does not plan shared units.

`R17`, `R52` and similar numbers in the docs and the code are rulings made during implementation:
narrower choices that follow from these decisions. Each is explained at the place that cites it.
