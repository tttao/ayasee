# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Platform and infrastructure engineers who adopt ayasee in their own repositories. They wire the
workflows and actions into OpenTofu/Terragrunt repos, push changes through dev, uat and prod, and use
the cockpit to see, across their estate, what is live, what is waiting to be promoted, which gates
are waiting for a person, and where there is drift. They read the documentation to set it up
(quickstart, brownfield onboarding, migration from environment branches) and to understand its
concepts (plan binding, risk gate, ledger, drift).

## Product Purpose

ayasee is a CI/CD framework that runs only on GitHub Actions. It promotes infrastructure as code
through stages behind gates: plan binding, risk-tiered approval gates, a deployment ledger, nightly
drift detection, and a static estate view with no server. Success means a platform team can promote
infrastructure changes safely and see the state of every environment without running or paying for
a server or SaaS.

The project has two web surfaces:

- **Documentation site** (`website/`, MkDocs Material, built from the repository's Markdown,
  published on GitHub Pages).
- **Cockpit** (`cockpit-site/`, Vite + TypeScript, static): repositories as rows, environments as
  columns, rendered from one JSON snapshot (`schema/snapshot.v1.json`).

## Positioning

Every building block exists elsewhere, and ayasee says so. What it adds is a free, read-only estate
view with no server (repository by environment: live SHA, pending changes, waiting gates, drift,
findings) together with a risk gate that knows which Databricks changes silently revoke access. It
all runs in the adopter's own GitHub Actions. There is no SaaS, no backend and no orchestrator.

## Operating Context

- Adopters' repositories use one trunk with environment folders, and their GitHub Actions run every
  stage.
- A collector workflow writes the snapshot JSON, commits it to git, and the same file feeds the
  static site, a pinned issue and a job summary.
- Target environments include github.com, GitHub Enterprise Server, regulated and air-gapped
  estates. GHES behaviour is not yet verified.
- Reference adopter: `examples/databricks-azure/` (a platform team running Databricks on Azure).

## Capabilities and Constraints

- **The cockpit is read-only.** It never approves, syncs or re-drives. Each action is an https deep
  link into GitHub's own Actions, issues and code-scanning pages.
- **No external assets or fonts.** Surfaces load only system fonts and nothing from a third-party
  origin, so they work on GHES and air-gapped Pages.
- **The snapshot carries counts, addresses and links only.** It has no plan values, finding details
  or resource attributes. A renderer refuses a snapshot with any other `schema` value.
- Status: pre-alpha. The first release candidate is `v0.1.0-rc.2`, and governance is not settled. Known
  limitations are listed in `docs/concepts/architecture.md#known-limitations`.

## Brand Commitments

- Name: **ayasee**, always lowercase. It is "IaC" spelled the way it sounds: *ay-a-see*.
- Voice: honest and plain, as befits a pre-alpha project. It states limitations up front, names the
  tools that do something better, and dates claims ("As of 2026-11-05").
- Licence: Apache-2.0.

## Evidence on Hand

- Comparison matrix with dated, sourced claims: `docs/comparison.md`.
- Design decisions D1 to D14: `docs/governance/decisions.md`.
- DORA Art. 17 compliance mapping: `docs/compliance/dora-art17.md`.
- Snapshot fixture for the cockpit: `cockpit-site/test/fixtures/snapshot.json`.
- **Absent, never to be invented:** adopters, customer logos, testimonials, usage numbers,
  benchmarks, a published release, or GHES verification.

## Product Principles

1. **Say only what is true.** Pre-alpha means visible limitations, dated claims and no invented proof.
2. **Show the state, and let GitHub do the acting.** The interface observes and links out; it never
   becomes a control plane.
3. **Nothing to host.** Every surface works as static files from Pages on GHES or an air-gapped
   network, with no external fetches.
4. **Help the reader decide quickly.** The engineer scanning a matrix of repos and environments
   should find the cell that needs them in seconds.

## Accessibility & Inclusion

Target WCAG 2.2 AA. Status is never shown by colour alone: pills and cells carry a text label.
Both surfaces support light and dark colour schemes.
