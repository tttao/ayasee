# release-pinning Specification

## Purpose

How ayasee is released and how adopters pin it. A release is a tag push handled by
`.github/workflows/release.yml`; reusable workflows reference their own actions through a
placeholder that the release rewrites to the released source commit, and adopters pin the release
commit by SHA. Code: `scripts/set-ayasee-ref.sh`, `scripts/check-provenance.sh`,
`scripts/pin-release.sh`, `src/ayasee/releasepin.py`. Reference: `docs/guides/releasing.md`,
`docs/concepts/staged-run.md#where-the-actions-come-from`.

## Requirements

### Requirement: Own actions referenced by pinned path
The reusable workflows SHALL reference ayasee's own actions as `OWNER/ayasee/actions/<x>@<sha>` lines marked `# AYASEE_REF`, all sharing one ref, and the committed workflow SHALL be a template until rewritten.

#### Scenario: Mixed refs
- **WHEN** two `# AYASEE_REF` lines carry different refs
- **THEN** the repository test that checks the placeholders fails

### Requirement: Release rewrite proves the pinned code is the released code
`scripts/set-ayasee-ref.sh <owner/repo> <source-sha>` SHALL rewrite every `# AYASEE_REF` line to `<owner/repo>/actions/<x>@<source-sha>` and SHALL refuse unless `git diff --quiet <source-sha> HEAD -- actions/ policy/ src/` shows no difference; `--local` SHALL rewrite to `./actions/<x>` for in-repository runs.

#### Scenario: Source drifted
- **WHEN** `actions/` differs between the source SHA and `HEAD`
- **THEN** the script refuses to rewrite

### Requirement: Provenance gate first
The first job of the release workflow SHALL check that the commit is an ancestor of `origin/main` and run `scripts/check-provenance.sh`, which SHALL fail unless the PROVENANCE licence line, `pyproject.toml` licence and `LICENSE` agree and a non-empty `NOTICE` exists; no other job SHALL run before it succeeds.

#### Scenario: Licence mismatch
- **WHEN** `pyproject.toml` declares a licence different from `PROVENANCE.md`
- **THEN** the release stops in its first job

### Requirement: Tag-only writes, dry-run dispatch
The release workflow SHALL fail the build when the tag differs from `v` plus `__version__`, SHALL perform write steps only on a tag ref, and a manual `workflow_dispatch` SHALL be a dry run only (R69).

#### Scenario: Manual run with dry-run false
- **WHEN** the release workflow is dispatched with `dry-run: false`
- **THEN** it fails in the first job

### Requirement: Release outputs
A release SHALL produce the wheel and sdist (GitHub release and PyPI), the cockpit site tarball, a build provenance attestation for each of the three files, a verified `release/<tag>` branch with one extra commit pinning the own-action references, and release notes naming the source SHA and the release SHA.

#### Scenario: Adopter pin
- **WHEN** an adopter pins a release
- **THEN** they pin the release SHA, the head of `release/<tag>`
