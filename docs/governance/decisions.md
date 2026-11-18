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
  `reviewers.prod` of `.ayasee.yaml` **read at `main`**, and was already there at the last
  approved commit (so a change cannot add its own approver); that they have write access; that
  the commit is on `main` and its newest unexpired plan comes from a successful `iac-staged` run
  on `main` whose manifest has exactly that digest; that they did not push, write or merge the
  change, nor write any commit since the last approved one (unless `solo_mode`, at both commits);
  and the guard (the commit is the one live in the stage before prod; no stop-the-line issue).
- It then applies exactly the approved encrypted plan and records the approver in the ledger.

**Where reviewer trust is anchored.** An empty prod plan goes live without an approval (the
in-sync path), so the commit live in prod is not a trusted reference. The anchor is the last
**approved** prod apply, the newest ledger record that carries an approver. A reviewer must be in
`reviewers.prod` both at `main` and at that approved commit, `solo_mode` counts only when both
have it, and independence covers every commit since that approval. Before the first approved apply
(or while the last one predates the workflow gate) there is no anchor: `main`'s list alone
decides, and the run prints `DEVIATION: bootstrap trust: ...`. That first approval is the one to
watch.

**A change of the gate settings always needs an approval.** With the workflow gate on either side
(the commit planned, or the last approved commit), a change of `gate_mechanism`,
`reviewers.prod` or `solo_mode` against the last approved commit is never promoted in sync, even
when its plan is empty: `prod-plan` reports `gate-changed` and the run goes to `prod-approval` (or,
when the commit sets the environment gate, to the `prod` job and its GitHub reviewers). The
approval is recorded in the ledger and becomes the new anchor. So:

- a change that only adds a reviewer, or only turns `solo_mode` on, waits for a reviewer of the
  previous list, and the person added cannot approve it (they are not listed at the anchor);
- leaving the workflow gate for `environment` needs the approval of the `prod` environment's
  GitHub reviewers; on a plan without them `prod-gate` refuses every such run, so it cannot be
  used to skip the gate;
- entering the workflow gate is the first approval, under bootstrap trust.

A change of the environment gate alone (both sides `environment`) keeps the in-sync path.

**Known limits.**

- The check that a plan comes from `iac-staged` is a path pattern on the run's referenced
  workflows (`*/.github/workflows/iac-staged.yml@...`): a caller on `main` that pointed at a fork
  would pass. A reusable workflow cannot compare it with its own repository and ref.
- A commit whose author GitHub cannot map to a login is reported, not refused: its author is not
  checked for independence.
- Bootstrap trust, above, until the first approved apply.

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

Break-glass (D3) has no workflow-gate form yet: in this mode `iac-breakglass` refuses to run.

**The pull-request plan of shared units.** With the environment gate, `plan-shared` of `iac-pr`
runs a pull request's code with a read-only prod identity after a prod reviewer approves its
`prod-pr-plan` job. Without environment reviewers nothing can hold a job of the pull request's own
run: that run uses the pull request's workflow file, so any check ayasee put in it could be
removed, and an environment that accepts pull-request branches hands its credentials to whichever
job names it. In this mode the plan therefore runs **from `main`**:

- `iac-pr` does not plan shared units. When a pull request changes some, it warns and its "Shared
  prod units plan" comment gives the pull request number and head SHA to authorise. It reads the
  mode at the pull request and at its base commit, so a pull request cannot switch its own plan
  back on.
- A prod reviewer reads the diff and starts `iac-pr-shared` on `main` with `pr` and `sha`. Its
  `verify` job (no environment, no secret) refuses unless the run is a `workflow_dispatch` on
  `refs/heads/main` started and not re-run by one person; the configuration **of `main`** has
  `gate_mechanism: workflow` and `shared_units` and lists that person in `reviewers.prod`; they
  have write access; the pull request is open, from this repository (never a fork), into `main`,
  and its head is exactly `sha`; they did not open it nor author or commit any of its commits
  (unless `solo_mode`); and `prod-pr-plan` deploys from `main` only.
- Its `plan` job runs in `prod-pr-plan`, which in this mode carries no reviewer and deploys from
  `main` only: GitHub's branch policy keeps every other branch's workflows away from its read-only
  credentials, and `main`'s workflow file is the one that runs the checks. It checks out exactly
  `sha` and plans the shared units that commit changes, read-only, as `plan-shared` does. A push
  after the authorisation is not planned: the comment says the pull request moved on, and the new
  head needs a new run.

| Repository | Pull-request plan of shared units with `gate_mechanism: workflow` |
|---|---|
| Public, or private on GitHub Enterprise | `iac-pr-shared`, as on Pro or Team; the default gate keeps it in `iac-pr` behind GitHub's reviewers |
| Private on GitHub Pro or Team | `iac-pr-shared`: the read-only prod credentials reach only jobs on `main` in `prod-pr-plan`, and a listed reviewer, checked by ayasee, decides which head of which pull request runs with them |
| Private on GitHub Free | **Not available.** `prod-pr-plan` cannot be restricted to `main`, so any branch's workflow could use it without these checks, and with no environment secrets or variables its credentials could not differ from the prod apply's (one repository `CLOUDFLARE_API_TOKEN`, one `AZURE_CLIENT_ID`): the reviewer would be running pull-request code with the prod write credentials. The bootstrap refuses `shared_units_pr_plan`, `iac-pr-shared` refuses to run, `ayasee doctor` fails on its caller. Shared units are first seen in `prod-plan` after the merge, at the approval issue |

What it keeps: the identity, the read-only plan and the comment rules of `plan-shared`; a prod
reviewer who is not an author of the pull request decides before its code runs with that identity,
as with `prevent_self_review`; and the decision binds one commit.

**Known limits of the pull-request plan in this mode.**

- The plan job runs on `main`'s ref. The pull request's code therefore runs in a job whose Actions
  cache scope is `main`'s (in `iac-pr` it is the pull request's merge ref), and whose OIDC token
  carries `ref: refs/heads/main` (its subject is `environment:prod-pr-plan`). Code that escapes
  the plan step can write to `main`'s Actions cache: ayasee's workflows on `main` restore none,
  but another workflow of the repository that restores a cache could run what was planted. Do not
  restore Actions caches in workflows that hold prod credentials, and never federate a prod
  identity on a `ref:refs/heads/main` subject.
- Who may authorise is read at `main` only, with no ledger anchor (the plan applies nothing): a
  change that adds a reviewer reaches `main` through `main`'s rulesets.
- Independence covers who opened the pull request and the authors and committers GitHub reports
  for its commits. Who pushed a commit is not visible; a commit without a GitHub login is
  reported, not refused; a pull request of 250 commits or more is refused (the API lists no more).
- The comment is advisory: the pull request's code produced the plan it summarises.

`R17`, `R52` and similar numbers in the docs and the code are rulings made during implementation:
narrower choices that follow from these decisions. Each is explained at the place that cites it.
