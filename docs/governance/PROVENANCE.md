# Provenance and licence

## Statement

`ayasee` is an independent work built from public sources only: public documentation, public
repositories and regulation text. The sources that shaped the design are linked where they are used,
in [the comparison](../comparison.md), [the DORA mapping](../compliance/dora-art17.md) and the
concept pages. The design decisions are recorded in [decisions.md](decisions.md), and the
implementation plan is in [`docs/superpowers/plans/`](../superpowers/plans/).

The repository contains no third-party names, tickets, runner labels, secrets, brand tokens, brand
assets or data. One third-party file is copied into it, unmodified: the Mermaid bundle that the
documentation site serves (see [Dependencies](#dependencies)). Tools such as OpenTofu,
Terragrunt, Trivy and Checkov are downloaded at run time under their own licences and are not
redistributed.

## Licence

- Licence: Apache-2.0

The copyright is held by the ayasee authors (see [`NOTICE`](../../NOTICE)), who license the work
to everyone under the [Apache License 2.0](../../LICENSE). You may use, modify and redistribute it,
commercially too, under the terms of that licence: keep the `LICENSE` and `NOTICE` files and state
the changes you make.

`scripts/check-provenance.sh` fails the release unless the line above, the `license` in
`pyproject.toml` and the `LICENSE` file agree and `NOTICE` is present
(see [releasing](../guides/releasing.md#1-the-provenance-gate)).

## Contributions

Contributions are accepted under the same licence (Apache-2.0, section 5): by opening a pull
request you state that you have the right to submit the change under it. See
[CONTRIBUTING.md](../../CONTRIBUTING.md).

## Dependencies

The Python dependencies (`pyproject.toml`, `uv.lock`) and the cockpit site's build tools
(`cockpit-site/package.json`) carry permissive licences (MIT, MIT-0, BSD, Apache-2.0, ISC, PSF, Blue Oak 1.0.0) or
MPL-2.0. They are installed from their registries, not vendored. The built cockpit site bundles no
third-party runtime library.

The documentation site serves one vendored file, so that its diagrams render without a CDN (the site
makes no third-party requests):

| File | Upstream | Version | Licence | SHA-256 |
|---|---|---|---|---|
| `website/javascripts/vendor/mermaid-11.17.2.min.js` | npm `mermaid`, `dist/mermaid.min.js` | 11.17.2 | MIT (text in `mermaid-11.17.2.LICENSE.txt` beside it) | `581ed7d74bd9048d0e3a91363927d72ef22942d7722546b27f7cc29e35390eb8` |

To update it, take `dist/mermaid.min.js` from `npm pack mermaid@<version>`, rename it with the new
version, change the path in `website/overrides/main.html`, and update this table and `NOTICE`.
