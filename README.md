# ayasee

A GitHub Actions-only CI/CD framework for staged, gated infrastructure-as-code promotion
(OpenTofu and Terragrunt first), with plan binding, risk-tiered approval gates and a
static, server-less estate view.

The name is "IaC" spelled the way it sounds: *ay-a-see*.

**Status: pre-alpha.** The workflows, actions, CLI, risk gate, policy pack, cockpit and a reference
example are implemented and tested. The latest release candidate is
[`v0.1.0-rc.3`](https://github.com/tttao/ayasee/releases); GitHub Enterprise Server
behaviour and the end-to-end sandbox are unverified, and governance is not settled. See the
[known limitations](docs/concepts/architecture.md#known-limitations) before you adopt it.

- Documentation site: <https://tttao.github.io/ayasee/>
- Documentation index: [`docs/README.md`](docs/README.md)
- Start: [quickstart](docs/guides/quickstart.md), [architecture](docs/concepts/architecture.md),
  [comparison with other tools](docs/comparison.md)
- Reference adopter repositories: [`examples/databricks-azure/`](examples/databricks-azure/README.md)
  (Azure, OIDC) and [`examples/cloudflare-s3/`](examples/cloudflare-s3/README.md) (Cloudflare and
  S3, no cloud identity, a tunnel shared by two stages)
- Security, contributing, governance: [`SECURITY.md`](SECURITY.md), [`CONTRIBUTING.md`](CONTRIBUTING.md),
  [`GOVERNANCE.md`](GOVERNANCE.md)
- Implementation plans: [`docs/superpowers/plans/`](docs/superpowers/plans/)
- Design decisions D1 to D14: [`docs/governance/decisions.md`](docs/governance/decisions.md)
- Provenance and licence: [`docs/governance/PROVENANCE.md`](docs/governance/PROVENANCE.md)

Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE).
