# ayasee documentation

ayasee promotes infrastructure-as-code through ordered stages (dev, uat, prod) from one `main`
branch, with GitHub Actions only: no server, no SaaS control plane. Prod applies the exact plan a
reviewer approved, a risk gate shows what a change does to access, and a static cockpit shows the
state of the estate.

The project is **pre-alpha**. Read [Known limitations](concepts/architecture.md#known-limitations)
before you adopt it, and [the comparison](comparison.md) to check it is the right tool.

## Start here

| If you want to... | Read |
|---|---|
| Understand the design in ten minutes | [Architecture](concepts/architecture.md) |
| Get from zero to a first green run | [Quickstart](guides/quickstart.md) |
| See a complete adopter repository | [Databricks on Azure example](../examples/databricks-azure/README.md) |
| Decide whether to use ayasee at all | [Comparison](comparison.md) |

## Concepts

| Page | What it explains |
|---|---|
| [Architecture](concepts/architecture.md) | The execution plane and the observation plane, the components, the data flow, known limitations |
| [The staged run](concepts/staged-run.md) | `iac-staged`: jobs, environments, variables, the plan artifact, approval, redrive, supersession |
| [Plan binding](concepts/plan-binding.md) | Encrypted saved plans, the manifest, the replan fallback, the cascade rule, what each binding mode allows |
| [Pull-request checks](concepts/pr-checks.md) | `iac-pr`: scans, the dev-only read-only plan, the sticky comment, SARIF scrubbing |
| [Drift and re-scan](concepts/drift.md) | `iac-nightly`: drift at the live SHA, noise paths, prod drift holds prod |
| [The ledger](concepts/ledger.md) | The source of truth for what is live: stores, layout, the newest-of rule, the Deployments projection |
| [The risk gate](concepts/risk-gate.md) | Risk tiers, principal deltas, adopter rule files, the Rego pack |
| [The cockpit](concepts/cockpit.md) | The snapshot contract, renderers, history, who can see it |

## Guides

| Guide | Use it to |
|---|---|
| [Quickstart](guides/quickstart.md) | Reach a first green run with the bootstrap modules and the example |
| [Migrate from environment branches](guides/migrate-from-env-branches.md) | Retire long-lived `uat` and `prod` branches, one repository at a time |
| [Onboard a brownfield Databricks estate](guides/onboard-brownfield.md) | Bring existing Unity Catalog objects under Terraform with the exporter and import blocks |
| [Releasing ayasee](guides/releasing.md) | Maintainer runbook: provenance gate, version bump, tag, approval, attestation check |

## Reference

| Page | Content |
|---|---|
| [`.ayasee.yaml`](reference/ayasee-yaml.md) | Every configuration key, its default, and where it takes effect |
| [Databricks policy pack](reference/policy-pack.md) | The Rego rules `SG-DBX-001` to `SG-DBX-006` and how to run them locally |
| [Cockpit setup](cockpit/setup.md) | The GitHub App, the data repository, `repos.yaml`, the wrapper workflow, rate limits |
| [Azure stage identity module](../modules/azure-stage-identity/README.md) | One managed identity and federated credential per environment, with the minimum roles |
| [GitHub environments module](../modules/github-environments/README.md) | The environments the workflows expect, with reviewers and branch policies |
| [Snapshot schema](../schema/snapshot.v1.json) | The JSON Schema of `ayasee.snapshot/v1` |

## Compliance

| Page | Content |
|---|---|
| [DORA RTS 2024/1774 Article 17](compliance/dora-art17.md) | Control mapping, solo mode, break-glass in practice, the `prod-breakglass` environment |

## Example and end-to-end tests

| Page | Content |
|---|---|
| [Databricks on Azure example](../examples/databricks-azure/README.md) | A working layout: three units across three stages, bootstrap root, callers |
| [Ownership contract](../examples/databricks-azure/OWNERSHIP.md) | What Terraform owns and what Declarative Automation Bundles own |
| [End-to-end scenarios](../e2e/README.md) | The sandbox scenarios (implemented, not yet run against a real sandbox) |

## Project

| Page | Content |
|---|---|
| [Security policy](../SECURITY.md) | Reporting a vulnerability, supported versions, the threat model |
| [Contributing](../CONTRIBUTING.md) | Development setup, tests, commit style, the SHA-pinning rule |
| [Governance](../GOVERNANCE.md) | Maintainers and decisions |
| [Provenance and licence](governance/PROVENANCE.md) | Public-sources statement, the Apache-2.0 licence, contributions and dependency licences |
| [Design decisions](governance/decisions.md) | D1 to D14: the options considered and the default taken |

## Conventions in these pages

- `D1` to `D14` are [the design decisions](governance/decisions.md); `R17`, `R52` and similar are
  rulings made during implementation. They are cited so you can trace a behaviour to its reason.
- Where a page names a file such as `src/ayasee/ledger.py` or `actions/apply/apply.sh`, that file
  is the authority; the page describes it.
- These pages are also published as a site at <https://tttao.github.io/ayasee/>. It is assembled
  by `scripts/build-docs-site.py` and built by the `docs` workflow; the Markdown here stays the
  source.
- Links in `docs/`, `README.md`, the root policy files and `examples/` are checked offline by
  `scripts/check-links.py` in CI.
