## ADDED Requirements

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
