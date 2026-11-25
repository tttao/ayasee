# adoption-tooling Specification

## Purpose

CLI commands that take a repository from nothing to a first green run: `ayasee init` scaffolds the
configuration, callers, `root.hcl` and bootstrap pinned to a release and chooses the gate from the
repository's GitHub plan; `ayasee doctor` checks everything a person must set up; `ayasee
import-plan` writes `import` blocks for existing objects. Code: `src/ayasee/scaffold.py`,
`src/ayasee/doctor.py`, `src/ayasee/importplan/`. Reference: `docs/guides/adopt.md`,
`docs/guides/onboard-brownfield.md`.
## Requirements
### Requirement: Pinned scaffold
`ayasee init` SHALL write `.ayasee.yaml`, the callers `iac-pr.yml`, `iac-staged.yml`, `iac-nightly.yml`, `iac-breakglass.yml` (and `iac-approve.yml` with the workflow gate), `root.hcl`, the bootstrap root and one directory per stage, all pinned to the release commit of the running version; `--ref` SHALL be accepted only for that release commit.

#### Scenario: Fresh repository
- **WHEN** `ayasee init --profile cloudflare-s3` runs in an empty repository
- **THEN** the callers reference the release SHA of that version and no placeholder remains

### Requirement: Safe re-runs
`ayasee init` SHALL skip files with identical content, show a diff and refuse a differing file without `--force`, and write nothing on any refusal. An existing `.ayasee.yaml` that loads SHALL be kept (even with `--force`) and its `stages`, `iac_root`, `environment_prefix` and `gate_mechanism` SHALL be used, refusing a flag that contradicts them; one that does not load SHALL be refused unless `--replace-config`.

#### Scenario: Existing configuration
- **WHEN** a repository already has a valid `.ayasee.yaml` with `stages: [test, prod]`
- **THEN** init keeps it and scaffolds callers and stage directories for `test` and `prod`

### Requirement: Gate chosen from the GitHub plan
`ayasee init` SHALL read the repository's visibility and plan (via `--repo` or the github.com `origin`, with `GITHUB_TOKEN` if set, never printed) and choose `environment` for a public or internal repository or a private Enterprise one, `workflow` with a warning for a private Free, Pro or Team one (with `reviewers.prod` set to `REPLACE_WITH_GITHUB_LOGIN` and the `iac-approve` caller), and `environment` with a note naming the flags when it cannot read a trustworthy answer. `--gate-mechanism` and `--plan` SHALL override with a warning when they contradict what was read.

#### Scenario: Plan unreadable
- **WHEN** init cannot read the repository's plan
- **THEN** it keeps `gate_mechanism: environment` and prints how to rerun with `--gate-mechanism workflow --plan ...`

### Requirement: Monorepo scaffold
With `--iac-root`, init SHALL place the estate under that directory and set `environment_prefix: iac-` in both `.ayasee.yaml` and the bootstrap, unless `--environment-prefix` says otherwise.

#### Scenario: IaC beside application code
- **WHEN** `ayasee init --iac-root infra/iac` runs
- **THEN** `.ayasee.yaml` has `iac_root: infra/iac`, `units_root: infra/iac/stages` and `environment_prefix: iac-`

### Requirement: Pin upgrade touches pins only
`ayasee init --upgrade` SHALL rewrite only the 40-character release SHA (and its `# ayasee vX.Y.Z` comment) in `uses: <source>/.github/workflows/iac-*.yml@<SHA>` lines and the bootstrap's `?ref=<SHA>` module sources, SHALL verify the new text differs from the old by the pins alone, and SHALL leave other sources, actions and `environment_prefix` untouched.

#### Scenario: Edited caller
- **WHEN** a caller has custom `paths:` and the pin is upgraded
- **THEN** only the SHA and version comment change

### Requirement: Doctor checks the setup in one pass
`ayasee doctor` SHALL first check that the repository's plan can hold the configured gate and stop with one failure if it cannot, then report in one pass every missing or wrong item: environments (prefixed) and their reviewers and `main`-only policies, secrets missing or set at repository level, variables, plan-key and ledger-key secrets passed and held as environment secrets of the right environments, `reviewers.*` logins with write access, callers unpinned or pinned to an older release, the ruleset on `main`, and (with `--lock-check`) state buckets that ignore the lock condition; it SHALL warn when a non-ayasee workflow names one of ayasee's environments, and exit 0 only when nothing is missing.

#### Scenario: Secret at repository level
- **WHEN** `AYASEE_PLAN_PASSPHRASE` exists as a repository secret
- **THEN** doctor reports it and exits non-zero

### Requirement: Import blocks without guessing
`ayasee import-plan --plan-json F` SHALL, for each planned create of a supported type, list live objects through the provider API with GET requests only and write `import { to = <address>, id = "<id>" }` to `imports.tf` for the unique match; when two live objects match one address, one object matches two addresses, or any API error occurs, it SHALL write nothing and exit 1; unsupported types or values unknown at plan time SHALL be listed as `skip`. Lookups SHALL exist for the Cloudflare DNS records, tunnel configuration, and Access applications and policies.

#### Scenario: Ambiguous DNS record
- **WHEN** two live DNS records match one planned record
- **THEN** no `imports.tf` is written and the command exits 1

### Requirement: Code scanning chosen from the repository
`ayasee init` SHALL read whether the repository can take a SARIF upload (a public repository can; otherwise `security_and_analysis` showing GitHub Code Security or Advanced Security enabled or disabled, else one read of the code-scanning alerts API, whose "not enabled" refusal means unavailable) and SHALL write `code-scanning: false` into the `iac-pr` and `iac-nightly` callers when it is unavailable, or when it cannot be confirmed for a private repository whose plan is Free, Pro or Team (read or passed with `--plan`), with a note saying why; it SHALL leave the callers uploading (the default) when code scanning is available or when nothing says the repository is private, with a note when it could not confirm. `--code-scanning` and `--no-code-scanning` SHALL override, with a warning when they contradict what was read; `init --upgrade` SHALL refuse them.

#### Scenario: Private repository on GitHub Free
- **WHEN** `ayasee init` reads a private repository whose code-scanning API answers "Code scanning is not enabled for this repository"
- **THEN** the `iac-pr` and `iac-nightly` callers pass `code-scanning: false` and init prints a note saying the SARIF stays a run artifact

#### Scenario: Public repository
- **WHEN** `ayasee init` reads a public repository
- **THEN** the callers pass no `code-scanning` input and the SARIF is uploaded

### Requirement: Doctor checks code scanning
When an `iac-pr` or `iac-nightly` caller does not pass `code-scanning: false`, `ayasee doctor` SHALL check, with `GITHUB_TOKEN`, that the repository can take a SARIF upload, report ok when it can, and warn, naming the callers and how to turn the upload off, when code scanning is unavailable or cannot be confirmed (an API error is never reported as ok); without a token or repository it SHALL report the check as skipped. When every such caller passes `code-scanning: false` it SHALL report ok without calling the API and say that findings stay run artifacts.

#### Scenario: Upload on a repository without code scanning
- **WHEN** the `iac-pr` caller passes no `code-scanning` input and the code-scanning API answers "Code scanning is not enabled for this repository"
- **THEN** doctor warns that `upload-sarif` fails on every pull request and names `code-scanning: false`

#### Scenario: Code scanning unconfirmed
- **WHEN** the code-scanning API answers an error that does not say whether it is enabled
- **THEN** doctor warns that it cannot confirm code scanning, never ok

### Requirement: Import blocks for a whole plan directory
`ayasee import-plan --plan-dir D --out-dir O --unit-prefix P --summary S` SHALL read every `<unit>.json` of `D` (the plan action's `json-dir`, `/` written as `__`), look every unit up before writing anything, write `O/P/<unit path>/imports.tf` for each unit with at least one unique match and no ambiguity, and write `S` listing, per unit, each planned create by address with its outcome and the file written. A unit with an ambiguous match SHALL get no file and the command SHALL exit 1; an API error or unreadable plan SHALL write no file and no summary and exit 1. `P` and each unit path SHALL be relative paths of plain names, else refused.

#### Scenario: Two units, one ambiguous
- **WHEN** the plan directory holds `dns.json` with one unique match and `access.json` with an ambiguous one
- **THEN** only `<prefix>/dns/imports.tf` is written, the summary lists both units, and the command exits 1

#### Scenario: Unsafe unit name
- **WHEN** the plan directory holds `a__..__b.json`
- **THEN** nothing is written and the command exits 1

### Requirement: Import caller scaffolded and checked
`ayasee init` SHALL write the `iac-import.yml` caller for the `cloudflare-s3` profile, pinned like the other callers and moved by `--upgrade`; it SHALL be dispatched by `workflow_dispatch` only and pass each secret by name. `ayasee doctor` SHALL check an `iac-import` caller like the other callers (pinned release SHA, secrets by name, never `secrets: inherit`), SHALL fail one whose workflow has a trigger other than `workflow_dispatch`, and SHALL check the provider secrets it passes in the `<first stage>-plan` environment.

#### Scenario: Caller on push
- **WHEN** a workflow calls `iac-import.yml` on `push`
- **THEN** doctor reports a failure naming `workflow_dispatch`

