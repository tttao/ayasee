# Running the e2e scenarios against GitHub Enterprise Server

Nothing in this file has been run. Per R8 no scenario has ever run against a real sandbox, on
github.com or on GHES, so every version below is **not yet verified**. This page tells you how to
produce the first verification and where to record it.

The scenarios and the driver are the ones in [`../README.md`](../README.md) (Task 17). Read that file
first, including the sandbox-only rules: the repository name must end in `-sandbox`.

## Feature detection

ayasee detects the platform instead of assuming it (D11). On the runner, in a repository of the
instance:

```bash
GITHUB_TOKEN=... GITHUB_API_URL=https://HOST/api/v3 ayasee features --json
```

The platform comes from the API host (R67): `api.github.com` is `dotcom`; `api.<sub>.ghe.com` is
`ghec-dr` (GitHub Enterprise Cloud with data residency, issuer `https://token.actions.<sub>.ghe.com`,
all feature flags false until verified); a host whose `/meta` has `installed_version` is `ghes`; any
other host fails with "unknown platform". It prints `platform`, `ghes_version`, `queue_max`, `deployment_false`, `lockfiles`, `private_pages`
and `oidc_issuer`. On GHES every flag is `false` unless a verified entry in
`GHES_FEATURE_MIN_VERSIONS` (`src/ayasee/features.py`) says otherwise. `iac-staged` runs this in
its `resolve` job and exposes the JSON as the `features` output. Nothing consumes it yet.

## What to set

| Item | Value |
|---|---|
| Base URL | `GITHUB_API_URL=https://HOST/api/v3` for the driver (`python -m e2e.run`) and for `ayasee`. Actions set it on the runner. A bare `https://HOST` also works for `ayasee features` |
| Driver env | `E2E_REPO` (`owner/name-sandbox`), `E2E_TOKEN`, `E2E_APPROVER_TOKEN` (a different identity), optional `E2E_WEBHOOK_URL`, plus `GITHUB_API_URL` above |
| OIDC issuer | `https://HOST/_services/token` (what `ayasee features` prints as `oidc_issuer`). Pass it as `issuer` to every `azure-stage-identity` module call and to the Databricks federation policy. Use the immutable `sub` format via `subject` where the instance issues it |
| Runners | Linux amd64 (the setup action installs linux amd64 tools only), `bash`, `curl`, `sha256sum`, `jq`, `git`; Python 3.12 is installed by uv. Self-hosted runners on the instance need network access to Azure and Databricks, to the tool mirror, and to PyPI (or an index mirror configured for uv) |
| Actions | The workflows use third-party actions pinned by SHA (`actions/checkout`, `astral-sh/setup-uv`, `azure/login` and others). On GHES make them available through GitHub Connect or mirror them to the instance, and keep the same SHAs |
| Ayasee refs | `scripts/set-ayasee-ref.sh` rewrites the `OWNER/ayasee` references. On GHES the reusable workflows and actions must live on the same instance (or be reachable through GitHub Connect) |

## Tool mirror

The setup and policy actions download OpenTofu, Terragrunt and conftest from `github.com` release
URLs. A GHES runner without internet access cannot do that. Set the `tool-mirror` input on the
reusable workflow:

```yaml
jobs:
  staged:
    uses: OWNER/ayasee/.github/workflows/iac-staged.yml@<sha>
    with:
      tool-mirror: https://mirror.example.com/github
```

The value replaces the `https://github.com` prefix, so the mirror must serve
`<tool-mirror>/opentofu/opentofu/releases/download/v<ver>/tofu_<ver>_linux_amd64.tar.gz` (and the same
path layout for `gruntwork-io/terragrunt` and `open-policy-agent/conftest`). It must be https with no
trailing slash. The SHA-256 checks are unchanged and mandatory, so a mirror cannot substitute a
different binary. For local tooling, use `scripts/install-dev-tools.sh --mirror URL` or
`AYASEE_TOOL_MIRROR`.

## Pages visibility caveats

Pages visibility cannot be detected through the API, and github.com Enterprise Cloud cannot be told
apart from github.com. `private_pages` is therefore only what you pass with
`ayasee features --private-pages true|false` (default `false`).

On GHES, Pages is instance-wide: the cockpit `pages` renderer is visible to every signed-in user, or
public, whatever the repository permissions say. Use the `issue` or `summary` renderer, or accept
that the published data holds counts, addresses and links only. See
[cockpit setup](../../docs/cockpit/setup.md#4-visibility-d8).

## Things to check on the first run

- `deployment: false`: whether PR plans can skip creating deployment records (`dev-plan`); until
  verified, `deployment_false` stays false on GHES.
- Merge queue (`queue_max`), dependency lockfile support (`lockfiles`) and reusable-workflow context
  properties (`github.job_workflow_sha`, R33).
- The `sub` claim format of the instance and the `oidc_issuer` in a token.
- Environment approval APIs used by `ayasee approvals` (pending deployments, run approvals).

## Version matrix

Fill a row when the scenarios have run end to end on that GHES release, then add the matching entry
to `GHES_FEATURE_MIN_VERSIONS` for each feature you saw working. Never record a version you have not
run.

| GHES version | Scenarios run | Date | Verified by | Verified |
|---|---|---|---|---|
| 3.14 | none | | | not yet verified |
| 3.15 | none | | | not yet verified |
| 3.16 | none | | | not yet verified |
| 3.17 | none | | | not yet verified |
| 3.18 | none | | | not yet verified |
