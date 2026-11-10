# Governance

ayasee is **pre-alpha**. This page records who decides and how, so nobody has to guess.

## Maintainers

The project has a single maintainer: the owner of the repository. There is no separate maintainer
list and no dedicated security contact address yet; both are added when a second maintainer joins.

The licence is Apache-2.0 and the copyright is held by the ayasee authors: see
[`docs/governance/PROVENANCE.md`](docs/governance/PROVENANCE.md). Releases follow
[the release runbook](docs/guides/releasing.md).

## How decisions are made

- The design decisions D1 to D14, with the options considered and the default taken, are in
  [`docs/governance/decisions.md`](docs/governance/decisions.md). Their effect on behaviour is
  described in [the docs](docs/README.md).
- A change to a safety default (plan binding, independent approval, the guard, what may leave a job,
  the snapshot contract) is made by changing the decision first, in an issue that states the reason
  and the trade-off, and then the code and docs together.
- Everything else follows the normal pull-request flow in [CONTRIBUTING.md](CONTRIBUTING.md): one
  reviewer other than the author, required checks green.
- Disagreements are resolved by the maintainer.
