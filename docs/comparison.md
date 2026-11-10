# Comparison with other tools

**As of 2026-11-05.** This page is built from a review of the tools' public documentation.
Star counts and licences come from the GitHub API on 2026-11-05; everything else is from the vendors'
public documentation on or before that date, linked below. Products change, and a matrix cell can be
out of date the day after. Check the source before you decide, and open an issue if a cell is wrong.

The short version: **every building block of ayasee exists elsewhere.** Plan binding, ordered
dispatch, drift and dashboards are all shipped by other tools, some of them better today. What the
review found missing is a free, server-less, read-only estate view (repository by environment: live SHA,
pending changes, gates waiting, drift, findings) together with a risk gate that knows which Databricks
changes silently revoke access. That combination is what ayasee tries to be, and it is young.

## Trunk practice: the honest wording

ayasee assumes one trunk with environment folders. That matches most vendor guidance, with one
exception, and this page does not claim more than the sources support.

> Microsoft and AWS favour trunk plus environment folders; Google's foundation blueprint is a
> current exception.

- AWS lists long-lived GitFlow-style branches as an anti-pattern that "add overhead … slow down the
  release pace" and recommends trunk-based development or GitHub flow
  ([AWS DevOps Guidance][aws-anti]). AWS Prescriptive Guidance names separate `./environments`
  folders in one repository as the "most common solution" for IaC and multiple trunk branches as the
  "least common" ([AWS Prescriptive Guidance][aws-pg]).
- Azure Well-Architected says to deploy IaC only through CI/CD and to use "the same IaC assets in
  various environments" ([Azure WAF][azure-waf]). DORA recommends three or fewer active branches and
  daily merges to trunk ([DORA][dora-tbd]); its evidence comes from application code, not IaC.
- Google's actively maintained `terraform-example-foundation` runs `plan` on feature branches and
  `apply` on merge to the `development`, `nonproduction` and `production` branches. It was pushed on
  2026-11-05 and is not archived ([GitHub][google-foundation]). Environment branches are a current,
  maintained practice in at least that blueprint.

## Matrix

| Tool | Runs where | Licence / cost | Ordered multi-env promotion | Plan binding | Risk gate | Drift | Estate dashboard | Fit vs "Actions-only" |
|---|---|---|---|---|---|---|---|---|
| **ayasee** (this project) | Your Actions only | Apache-2.0 | Yes, one run per merge | Encrypted saved plan plus manifest (OpenTofu); replan fallback not wired into the workflow yet | Named risk tiers with principal deltas, plus a Databricks Rego pack | Nightly at the live SHA, one issue per environment | Static, read-only snapshot (Pages, issue or summary) | Yes |
| **tfaction** | Actions only | MIT, 406 stars | No (changed directories) | Saved plan artifact | conftest | Issues, one per directory | No | Yes |
| **dflook actions** | Actions only | No LICENSE file detected | No | Re-plan must equal the PR plan | No | `*-check` actions | No | Yes |
| **Atmos Native CI / Atmos Pro** | Your Actions plus a SaaS app | Apache-2.0 CLI; Pro hosted | Dependency-ordered dispatch (Pro) | Stored plan plus semantic compare | OPA | Pro (scheduled) | Pro (hosted) | Partly |
| **Gruntwork Pipelines** | Your Actions | Commercial subscription | Patcher promotion of updates | Not documented | No | Scheduled, opens a PR | No | Yes, but paid |
| **ALZ accelerator** | Actions only | MIT | Single environment per pipeline | Artifact within the run | No | No | No | Yes (template) |
| **Terramate** | CLI in CI plus Cloud | MPL-2.0 CLI; Cloud proprietary | Ordering, no promotion | Not documented | Cloud policies | Needs Cloud | Cloud (paid tiers) | Partly |
| **Digger / OpenTaco** | CI plus an orchestrator backend | MIT (plus an `ee` directory, terms unknown) | No | Plan persistence | OPA | Yes | Cloud UI | No (backend) |
| **Terrateam / Stategraph** | Your runners plus a server | MPL-2.0; OSS capped at 3 active users | No | Apply invalidates plans | Cost threshold, OPA | Scheduled | Console | No (server) |
| **Atlantis** | Self-hosted server | Apache-2.0 | No | Plan hash | Custom | Alpha API | Locks and logs only | No |
| **Spacelift / HCP Terraform / env0 / Scalr** | SaaS (or self-hosted) | Proprietary | Stack dependencies / Stacks / Workflows | Same-run plan | OPA / Sentinel | Paid or metered | Yes | No |
| **Kargo** | Kubernetes | Apache-2.0 core; Terraform steps commercial | Yes (freight and stages) | `tf-apply` of a saved plan | No | No | Promotion UI | No |
| **Burrito / tofu-controller** | Kubernetes | Apache-2.0 | No | Not applicable | No | Periodic plan | Burrito layer UI | No |

The ayasee row describes the code in this repository, not a published release. Where a competitor
cell says "not documented", the vendor's public documentation did not say, which is not the same as
"absent".

## Use X instead if...

### tfaction

Use [tfaction][tfaction] if you want an established, MIT-licensed, Actions-only tool today, with
saved-plan apply, conftest policy and one drift issue per directory, and you do not need ordered
dev, uat, prod promotion in a single run, a risk tier at the gate or an estate view. Contributing a
staged-promotion module to tfaction is a credible alternative to a new project (D12); that option
stays open.

### dflook actions

Use [dflook's terraform-github-actions][dflook] if you want small, single-purpose actions (plan, apply,
check) you can compose yourself, with an apply that fails when the regenerated plan "is not found or
has changed". It has no promotion model, risk gate or dashboard, and at the date above the repository
showed no LICENSE file; check that before depending on it.

### Atmos and Atmos Pro

Use [Atmos / Atmos Pro][atmos] if you already organise infrastructure as Atmos stacks, or if you want
dependency-ordered dispatch across components and a cross-repository dashboard **now** and accept a
SaaS GitHub App as the control plane. Of the tools reviewed, Atmos Pro is the closest in scope. Its
control plane is hosted, which is the main thing ayasee avoids.

### Gruntwork Pipelines

Use [Gruntwork Pipelines][gruntwork] if you already buy Gruntwork, want vendor support and its Patcher
workflow for promoting updates, and a commercial subscription is acceptable. It runs in your Actions.
Plan binding is not documented, and it offers no risk gate or estate dashboard.

### Azure Landing Zones accelerator

Use the [ALZ accelerator][alz] if your goal is to deploy an Azure landing zone with Microsoft's
supported templates. It bootstraps pipelines (plan and apply jobs, separate environments, an
apply-approvers team), but it is a template for one environment per pipeline and is not a
multi-environment promotion framework.

### Terramate

Use [Terramate][terramate] if your problem is orchestrating many stacks (change detection, ordering,
code generation) and you are happy to use Terramate Cloud for recorded drift results and dashboards.
Its drift workflow needs Terramate Cloud to record results, and it has ordering but no promotion.

### Digger / OpenTaco

Use [Digger / OpenTaco][digger] if you want pull-request automation with locks and plan persistence
managed by an orchestrator backend and a cloud UI, and you accept a backend component. Its licence
has an `ee` directory whose terms the review could not determine.

### Terrateam and Stategraph

Use [Terrateam / Stategraph][stategraph] if you want a console with cost thresholds and scheduled
drift, run on your runners with a server, and a cap of three active users on the open-source edition
is acceptable (or you will pay).

### Atlantis

Use [Atlantis][atlantis] if you want the mature, widely known PR-comment workflow and your team is
willing to operate a self-hosted server. It binds apply to a plan by hash. Its drift API is alpha and
its view of an estate is locks and logs.

### The SaaS platforms: Spacelift, HCP Terraform, env0, Scalr

Use a SaaS platform ([Spacelift][spacelift], [HCP Terraform][hcp], [env0][env0], Scalr) if you want a
managed control plane with built-in policy (OPA or Sentinel), private workers, support and real
dashboards, and you accept the cost and the hosted service. Drift detection is a paid or metered
feature (for example Spacelift Starter and above with private workers; HCP health assessments in
Standard and Premium).

### Kargo

Use [Kargo][kargo] if you already run Kubernetes and a GitOps stack and want the best "freight moves
through stages" experience. Its `tf-plan` and `tf-apply` steps are only available on the Akuity
Platform, and it needs a cluster.

### Burrito and tofu-controller

Use [Burrito][burrito] or [tofu-controller][tofucontroller] if you want continuous, in-cluster
reconciliation of Terraform in a GitOps shape and already operate Kubernetes. They plan periodically
and have no ordered promotion.

## Use ayasee if...

- You are on GitHub Actions and want **no server and no SaaS control plane**, including in a regulated
  or air-gapped setting.
- You want a prod apply bound to the **exact approved plan**, with independent approval by default
  and a separate break-glass path with a post-approval record ([DORA mapping](compliance/dora-art17.md)).
- You run Databricks on Azure and want a gate that shows **who gains and who loses access**, not just
  "update in place".
- You want one trunk promoted dev, uat, prod in one run, with a ledger that says what is live.
- You want a read-only estate view from git, with a snapshot you can render anywhere.

## Where ayasee is weaker

- It is **pre-alpha** with a single maintainer.
  Established tools have users, support and years of bug reports. See [governance](../GOVERNANCE.md).
- The wired path is **OpenTofu, Terragrunt and Azure** (identities, Key Vault plan encryption, blob
  ledger). Plain Terraform is a secondary profile that `iac-staged` does not select yet.
- The risk rules and policy pack are **Databricks on Azure** specific. Other providers only get the
  generic destroy rule.
- GHES behaviour and the end-to-end sandbox are **unverified**, see
  [known limitations](concepts/architecture.md#known-limitations).
- The cockpit is read-only and not real time, and it is not a replacement for a vendor dashboard with
  an approval UI.
- Ordered promotion and subset-style binding are not differentiators: other Actions-native tools can
  assemble the first, and ayasee does not implement the weaker subset check at all.

## Sources

Competitors (checked 2026-11-05):
[tfaction][tfaction], [dflook][dflook], [Atmos CI][atmos-ci], [Atmos Pro][atmos],
[Atmos Pro drift][atmos-drift], [Gruntwork overview][gruntwork], [Gruntwork Pipelines actions][gruntwork-actions],
[ALZ accelerator][alz], [Terramate dashboard][terramate], [Terramate drift workflow][terramate-drift],
[Digger][digger], [Stategraph][stategraph], [Atlantis][atlantis], [env0 promotion][env0],
[Spacelift drift detection][spacelift], [HCP Terraform health][hcp], [HCP Explorer][hcp-explorer],
[Kargo tf-plan][kargo], [Burrito][burrito], [tofu-controller][tofucontroller].

Trunk practice: [AWS DevOps Guidance][aws-anti], [AWS Prescriptive Guidance][aws-pg],
[Azure Well-Architected][azure-waf], [DORA trunk-based development][dora-tbd],
[Google terraform-example-foundation][google-foundation].

[tfaction]: https://suzuki-shunsuke.github.io/tfaction/docs/
[dflook]: https://github.com/dflook/terraform-github-actions
[atmos-ci]: https://atmos.tools/ci
[atmos]: https://atmos-pro.com/docs
[atmos-drift]: https://atmos-pro.com/docs/configure/drift-detection
[gruntwork]: https://docs.gruntwork.io/2.0/docs/pipelines/concepts/overview
[gruntwork-actions]: https://docs.gruntwork.io/pipelines/overview/actions
[alz]: https://azure.github.io/Azure-Landing-Zones/accelerator
[terramate]: https://terramate.io/docs/cloud/dashboard/
[terramate-drift]: https://terramate.io/cli/automation/github-actions/drift-check-workflow
[digger]: https://github.com/diggerhq/digger
[stategraph]: https://github.com/stategraph/stategraph
[atlantis]: https://www.runatlantis.io/docs/custom-workflows
[env0]: https://docs.envzero.com/guides/admin-guide/environments/promotion-strategies
[spacelift]: https://docs.spacelift.io/concepts/stack/drift-detection
[hcp]: https://developer.hashicorp.com/terraform/cloud-docs/workspaces/health
[hcp-explorer]: https://developer.hashicorp.com/terraform/cloud-docs/workspaces/explorer
[kargo]: https://docs.kargo.io/user-guide/reference-docs/promotion-steps/tf-plan
[burrito]: https://github.com/padok-team/burrito
[tofucontroller]: https://github.com/flux-iac/tofu-controller
[aws-anti]: https://docs.aws.amazon.com/wellarchitected/latest/devops-guidance/anti-patterns-for-software-component-management.html
[aws-pg]: https://docs.aws.amazon.com/prescriptive-guidance/latest/designing-a-devsecops-mechanism/branching-strategies.html
[azure-waf]: https://learn.microsoft.com/en-us/azure/well-architected/operational-excellence/infrastructure-as-code-design
[dora-tbd]: https://dora.dev/capabilities/trunk-based-development/
[google-foundation]: https://github.com/terraform-google-modules/terraform-example-foundation
