# Design decisions D1 to D14

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

`R17`, `R52` and similar numbers in the docs and the code are rulings made during implementation:
narrower choices that follow from these decisions. Each is explained at the place that cites it.
