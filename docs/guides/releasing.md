# Releasing ayasee

The maintainer runbook. A release is a tag push; `.github/workflows/release.yml` does the rest.
Nothing here is automatic for a pull request or a push to a branch. A manual run
(`workflow_dispatch`) is a dry run only (R69): it fails in the first job with `dry-run` set to
false, and every write step requires a tag ref.

## What a release produces

| Output | Where |
|---|---|
| Wheel and sdist | GitHub release assets and PyPI (`ayasee`) |
| Cockpit site tarball | GitHub release asset |
| Build provenance attestation for each of the three files | GitHub attestations |
| Release branch `release/<tag>` with one extra commit that pins the own-action references | the repository |
| Release notes naming both SHAs, with the CHANGELOG section | the GitHub release |

Two commits matter. The **source SHA** is the tagged commit, the code that is released. The
**release SHA** is the head of `release/<tag>`: the same code, with the `# AYASEE_REF` lines of
the workflows rewritten to `<owner>/ayasee/actions/<x>@<source SHA>` by
[`scripts/set-ayasee-ref.sh`](../../scripts/set-ayasee-ref.sh)
(see [where the actions come from](../concepts/staged-run.md#where-the-actions-come-from)). Adopters
pin the **release SHA**.

## 1. The provenance gate

The first job of the workflow also checks that the commit is an ancestor of `origin/main` (for a
manual dry run too), then runs `scripts/check-provenance.sh`. The script fails unless:

- the "Licence" section of [`docs/governance/PROVENANCE.md`](../governance/PROVENANCE.md) holds
  the line `- Licence: Apache-2.0` (or `MIT`), exactly;
- that licence equals the `license` in `pyproject.toml` and the `LICENSE` file;
- a non-empty `NOTICE` file is present.

No other job runs before it succeeds. Run it locally first:

```bash
scripts/check-provenance.sh
```

Before the first release also update the pre-alpha wording in `README.md`, because it describes
the state before release.

## 2. Bump the version

1. In `src/ayasee/__init__.py` set `__version__` to the version without the `v` (for a release
   candidate `0.1.0-rc.1`).
2. In `CHANGELOG.md` change `## [0.1.0] - unreleased` to the date, and move anything under
   `[Unreleased]` into the new section. A release candidate uses the section of its final version.
3. Run `uv run pytest` and `uv run ruff check`.
4. Merge to `main`. The commit must be an ancestor of `origin/main`; the first job refuses
   otherwise.

The build job fails when the tag does not equal `v` plus `__version__`.

Names of release candidates differ by file: hatch normalises the version (PEP 440), so the wheel
and sdist of `0.1.0-rc.1` are `ayasee-0.1.0rc1-...`, while the cockpit tarball keeps the tag
spelling, `ayasee-cockpit-site-0.1.0-rc.1.tar.gz`. Use those names with `gh attestation verify`.

## 3. Try it without side effects

Run the workflow from the Actions tab with **dry-run** left at its default (true). It runs the
provenance gate, builds the wheel, sdist and cockpit site, and makes the pin commit locally. It
skips the push, the attestation, the release and PyPI. You can do the same build steps on your
machine:

```bash
uv build --out-dir /tmp/dist && unzip -l /tmp/dist/*.whl | grep rules/databricks-azure.yaml
(cd cockpit-site && npm ci --ignore-scripts && npm run build)
```

## 4. One-time repository setup

Mandatory, because the pin job pushes a branch and the workflow trusts the tag:

- **Tag ruleset on `v*`**: only maintainers can create tags, and nobody can move or delete one.
- **Branch protection on `release/*`**: no deletion and no force push. The release SHA that
  adopters pin must stay reachable.
- **Environment `pypi`**: Settings, Environments, add required reviewers (a person other than the
  one who pushes the tag), and restrict deployment to tags `v*`.
- **PyPI trusted publisher** for the project `ayasee`: owner and repository of this
  repository, workflow `release.yml`, environment `pypi`. No API token is ever created or stored.

## 5. Push the tag

```bash
git switch main && git pull --ff-only
git tag -s v0.1.0 -m "ayasee v0.1.0"
git push origin v0.1.0
```

Try `v0.1.0-rc.1` first; a tag with a hyphen is published as a pre-release.

## 6. Approve the pypi environment

After `build`, `pin`, `attest` and `github-release` succeed, the `pypi` job waits for the
reviewers. Check the release page and the attestation (below) before approving: a PyPI upload
cannot be replaced, only yanked.

## 7. Verify

```bash
gh release download v0.1.0 --repo <owner>/ayasee --dir verify
gh attestation verify verify/ayasee-0.1.0-py3-none-any.whl --repo <owner>/ayasee
gh attestation verify verify/ayasee-0.1.0.tar.gz --repo <owner>/ayasee
gh attestation verify verify/ayasee-cockpit-site-0.1.0.tar.gz --repo <owner>/ayasee
```

Optionally re-verify the attestations before you approve the `pypi` environment, as above, using
the files from the release page: the upload cannot be undone.

Then point the example repository at the release SHA and run the end-to-end scenarios
(see [`e2e/README.md`](../../e2e/README.md)) before you announce the release.

## 8. How adopters pin

Adopters reference the reusable workflows by the **release SHA** from the release notes, never by
tag or branch, and keep the version in a comment:

```yaml
uses: <owner>/ayasee/.github/workflows/iac-staged.yml@<release SHA> # ayasee v0.1.0
```

The own-action references inside that commit already point at the source SHA. The
[quickstart](quickstart.md#2-pin-an-ayasee-release) shows the `sed` that replaces `OWNER` and the
placeholder SHA in an adopter's callers. The `setup` action installs the CLI from the pinned
ayasee checkout; PyPI is for people who want the CLI on their own machine.

## Distribution

The supported distribution is the public repository `tttao/ayasee`: adopters in any account or
organization call its reusable workflows and actions at a release SHA. GitHub shares workflows and
actions of a private repository only with repositories of the same owner (or, for an
organization, within it), so the repository stays public.

### Mirrors for GHES and air-gapped estates

An estate whose runners cannot reach github.com keeps a mirror and pins the mirror's own SHAs:

1. Mirror the release branch and the tag without rewriting history:
   `git clone --bare https://github.com/tttao/ayasee && git -C ayasee.git push --mirror https://<host>/<org>/ayasee`,
   or push only `release/<tag>`, `main` and the tag.
2. Verify the mirror: `git -C <mirror clone> rev-parse release/<tag>` must equal the release SHA in
   the release notes, and `git -C <mirror clone> rev-parse <tag>^{commit}` the source SHA. Git
   object IDs are content hashes, so equal SHAs mean equal code.
3. The release commit pins the actions as `tttao/ayasee/actions/<x>@<source SHA>`. On the mirror,
   rewrite that owner on top of the release branch, once per reusable workflow:
   `for f in .github/workflows/{iac-staged,iac-pr,iac-nightly,iac-breakglass,cockpit-collect}.yml; do scripts/set-ayasee-ref.sh <org>/ayasee <source SHA> "$f"; done`,
   then commit and pin that commit. The script refuses unless the code is identical to the source
   SHA.
4. Follow ayasee releases on your own cadence; each update repeats steps 1 to 3.

## If something fails

- **Provenance gate fails**: the confirmation is incomplete. Nothing was built.
- **Version mismatch**: delete the tag locally and remotely, fix `__version__`, tag again.
- **A job after `pin` fails**: `release/<tag>` may already exist. Delete the branch before a
  re-run, or bump to the next version.
- **PyPI rejected**: check the trusted publisher settings (owner, repository, workflow, environment).
