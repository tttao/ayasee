# toolchain-setup Specification

## Purpose

`actions/setup` installs the pinned engine, Terragrunt, uv and the ayasee CLI on the runner with
checksum verification and no dependency cache, and `ayasee features` reports platform capabilities
instead of assuming them. Code: `actions/setup/`, `src/ayasee/features.py`.

## Requirements

### Requirement: Pinned, checksum-verified tools
The setup action SHALL install OpenTofu (default 1.13.9) and Terragrunt (default 1.2.1) only after verifying their SHA-256 against the pinned inputs, install a pinned uv, and install the ayasee CLI; it SHALL NOT install Terraform.

#### Scenario: Checksum mismatch
- **WHEN** the downloaded OpenTofu archive does not match `tofu-sha256`
- **THEN** the action fails and nothing is installed

### Requirement: No dependency cache
The setup action SHALL never restore or save a dependency cache (uv cache disabled, `UV_NO_CACHE=1`), because pull-request code could poison it (R40).

#### Scenario: PR run
- **WHEN** setup runs in a pull-request job
- **THEN** no cache is restored or saved

### Requirement: Tool mirror
The setup and policy actions SHALL accept an optional https `tool-mirror` base URL that replaces `https://github.com` in tool download URLs, with the SHA-256 checks still applied.

#### Scenario: Air-gapped runner
- **WHEN** `tool-mirror` points at an internal mirror
- **THEN** tools are downloaded from it and still checksum-verified

### Requirement: Platform feature detection
`ayasee features --json` SHALL detect the platform from the API host and `GET /meta` (`api.github.com` is github.com, `api.<sub>.ghe.com` is GHEC with data residency, a `/meta` with `installed_version` is GHES, any other host fails as "unknown platform"), report the OIDC issuer and the flags `queue_max`, `deployment_false`, `lockfiles` and `private_pages`, and report every flag false on GHES and GHEC-DR unless a verified version entry exists. `iac-staged` SHALL expose the result as the informational `resolve` output `features`, falling back to `{}` with a warning on failure.

#### Scenario: GHES
- **WHEN** `/meta` returns `installed_version`
- **THEN** every feature flag is reported false and the issuer is `https://HOST/_services/token`
