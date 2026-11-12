---
title: Staged, gated IaC promotion on GitHub Actions
hide:
  - navigation
  - toc
---

<div class="ay-hero" markdown>

# ayasee

<p class="ay-tagline">One trunk. One run per merge. Dev, uat, then the exact plan a reviewer
approved, applied to prod.</p>

ayasee is a framework of reusable GitHub Actions workflows for promoting OpenTofu and Terragrunt
changes through ordered stages. There is no server and no SaaS control plane: everything runs in
your own Actions, with OIDC identities and no stored cloud secrets.

[Quickstart](docs/guides/quickstart.md){ .md-button .md-button--primary }
[Architecture](docs/concepts/architecture.md){ .md-button }
[Compare with other tools](docs/comparison.md){ .md-button }
{ .ay-actions }

<p class="ay-status"><strong>Status: pre-alpha.</strong> Implemented and tested; first release
candidate <code>v0.1.0-rc.2</code>. Read the <a href="docs/concepts/architecture/#known-limitations">known limitations</a>
before you adopt it.</p>

</div>

## What a merge does

<figure class="ay-route">
<ol class="ay-route__line" aria-label="One run per merge, in order">
<li class="ay-stop"><span class="ay-stop__name">Pull request</span><span class="ay-stop__does">scans, read-only dev plan, risk summary</span></li>
<li class="ay-stop"><span class="ay-stop__name">push to main</span><span class="ay-stop__does">after review and merge</span></li>
<li class="ay-stop"><span class="ay-stop__name">dev</span><span class="ay-stop__does">plan, apply, converge check</span></li>
<li class="ay-stop"><span class="ay-stop__name">uat</span><span class="ay-stop__does">plan, apply, converge check</span></li>
<li class="ay-stop"><span class="ay-stop__name">prod-plan</span><span class="ay-stop__does">encrypted plan, policy pack, risk tiers</span></li>
<li class="ay-stop ay-stop--gate"><span class="ay-stop__name">prod gate</span><span class="ay-stop__does">independent approval</span></li>
<li class="ay-stop"><span class="ay-stop__name">prod</span><span class="ay-stop__does">verify the plan, apply it</span></li>
</ol>
<figcaption class="ay-route__observe">
<span class="ay-route__from">dev, uat and prod record to</span>
<ol class="ay-route__line ay-route__line--observe" aria-label="What is recorded, and where it is read">
<li class="ay-stop ay-stop--record"><span class="ay-stop__name">ledger</span><span class="ay-stop__does">what is live</span></li>
<li class="ay-stop ay-stop--record"><span class="ay-stop__name">cockpit</span><span class="ay-stop__does">read-only estate view</span></li>
</ol>
</figcaption>
</figure>

Each stage starts with a guard that fails closed: the previous stage must have this commit live and
converged, and no stop-the-line issue may be open. Details: [the staged run](docs/concepts/staged-run.md).

## What you get

<div class="grid cards" markdown>

-   **Plan binding**

    ---

    Prod applies the encrypted plan file the reviewer saw, checked against a SHA-256 manifest, the
    engine version and the provider lock. A stale plan stops the run and asks for a new approval.
    It is never re-planned silently.

    [Plan binding](docs/concepts/plan-binding.md)

-   **A risk gate that reads access changes**

    ---

    Every change in the prod plan gets a tier. Destroys and replacements come first, and a grants
    or permissions update lists the principals that gain or lose access, not just "update in place".

    [The risk gate](docs/concepts/risk-gate.md)

-   **A ledger of what is live**

    ---

    Each stage records the commit, the run, the per-unit result and the approvers in your backend.
    GitHub Deployments is a projection of it. The guard and the drift check read it.

    [The ledger](docs/concepts/ledger.md)

-   **Drift at the live commit**

    ---

    A nightly plan per environment at the commit the ledger says is live, with one issue per
    environment. Prod drift holds prod until someone looks at it.

    [Drift and re-scan](docs/concepts/drift.md)

-   **Break-glass with a record**

    ---

    One emergency path into prod: an incident reference, a separate environment with its own
    reviewers and identity, and a post-approval issue opened on every use.

    [How break-glass maps to DORA Article 17](docs/compliance/dora-art17.md)

-   **A cockpit without a server**

    ---

    A static repository-by-environment matrix built from JSON snapshots in git: live commit,
    pending changes, gates waiting, drift and findings. It never approves or applies anything.

    [The cockpit](docs/concepts/cockpit.md)

</div>

## What adopting it looks like

Environments are folders, not branches. A repository calls the reusable workflows, pinned by
commit SHA, and describes its stages in one file.

=== "Layout"

    ```text
    .ayasee.yaml
    root.hcl
    stages/
      dev/   workspace/  unity-catalog/  grants/
      uat/   workspace/  unity-catalog/  grants/
      prod/  workspace/  unity-catalog/  grants/
    .github/workflows/
      pr.yml  main.yml  nightly.yml  breakglass.yml
    ```

=== ".github/workflows/main.yml"

    ```yaml
    name: main
    on:
      push:
        branches: [main]

    permissions: {}

    jobs:
      staged:
        permissions:
          contents: read
          id-token: write
          deployments: write
          issues: write
          actions: read
        uses: OWNER/ayasee/.github/workflows/iac-staged.yml@<release SHA>
        secrets:
          teams-webhook: ${{ secrets.TEAMS_WEBHOOK }}
    ```

=== ".ayasee.yaml"

    ```yaml
    stages: [dev, uat, prod]
    engine: opentofu
    units_root: stages

    ledger:
      url: azure-blob://<account>.blob.core.windows.net/ledger?prefix=example

    gate: always
    allow_cascade: false
    ```

The complete adopter repository, with the bootstrap root that creates the environments and the
identities, is the [Databricks on Azure example](examples/databricks-azure/README.md).

## Design choices worth knowing first

<div class="ay-wide" markdown>

| Choice | What it means for you |
|---|---|
| GitHub Actions only | Nothing to host or pay for beyond Actions. It also means no approval UI of its own: approvals are GitHub environment reviews. |
| OpenTofu and Terragrunt first | Plan encryption needs OpenTofu. Plain Terraform is a secondary profile that the staged workflow does not select yet. |
| Azure and Databricks first | Identities, Key Vault plan encryption, the blob ledger, the risk rules and the Rego pack target Databricks on Azure. Other providers only get the generic destroy rule. |
| Independent approval by default | The person who triggers a run cannot approve it and administrators cannot bypass. `solo_mode` is an opt-in that is logged as non-compliant. |
| OIDC only | One managed identity per environment, bound to that environment's federated credential. No cloud secret is stored. |

</div>

The reasoning behind each default is recorded in the [design decisions](docs/governance/decisions.md),
and the page on [other tools](docs/comparison.md) says when one of them is the better choice.

## Where to go next

<div class="grid cards" markdown>

-   **Evaluate**

    ---

    [Architecture](docs/concepts/architecture.md)<br>
    [Comparison with other tools](docs/comparison.md)<br>
    [Security policy and threat model](SECURITY.md)

-   **Adopt**

    ---

    [Quickstart](docs/guides/quickstart.md)<br>
    [Migrate from environment branches](docs/guides/migrate-from-env-branches.md)<br>
    [Onboard a brownfield Databricks estate](docs/guides/onboard-brownfield.md)

-   **Look things up**

    ---

    [`.ayasee.yaml` reference](docs/reference/ayasee-yaml.md)<br>
    [Databricks policy pack](docs/reference/policy-pack.md)<br>
    [Full documentation index](docs/README.md)

</div>

<p class="ay-name">The name is "IaC" spelled the way it sounds: <em>ay-a-see</em>.</p>
