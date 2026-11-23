# policy-pack Specification

## Purpose

Rego policy packs answer "is this change allowed at all" on the plan JSON, inside the job that made
the plan. Two packs ship: Databricks (`policy/databricks/`, namespace `ayasee.databricks`) and
Cloudflare (`policy/cloudflare/`, namespace `ayasee.cloudflare`), run by `actions/policy` with
conftest. Reference: `docs/reference/policy-pack.md`.

## Requirements

### Requirement: Both packs on every plan, inside the job
The policy action SHALL run conftest with both namespaces on the plan JSON in the job that made the plan (PR `plan-first-stage` and `plan-shared`, `prod-plan`, break-glass), so the JSON never leaves the job; a `deny` SHALL fail the job.

#### Scenario: Deny on the PR plan
- **WHEN** the PR plan triggers a deny rule
- **THEN** `plan-first-stage` fails, `plan-dev` fails, and the sticky comment says "Policy pack: failed" with a run link

### Requirement: Stage and flags from the data file
The packs SHALL read the stage from `data.ayasee.stage` and flags from `data.ayasee.flags`; missing data SHALL mean stage `""` (not prod) and no flags. The workflows SHALL write the first stage for pull requests, `prod` for `prod-plan`, PR shared plans and break-glass, and an empty flag list.

#### Scenario: Warn outside prod
- **WHEN** `SG-DBX-003` triggers with stage `dev`
- **THEN** it is a warning, not a failure

### Requirement: Databricks rules
The Databricks pack SHALL implement `SG-DBX-001` (deny `ALL_PRIVILEGES` to `account users`), `SG-DBX-002` (non-`ISOLATED` catalog: deny in prod, warn elsewhere), `SG-DBX-003` (workspace public network access: deny in prod, warn elsewhere), `SG-DBX-004` (warn on `skip_validation`), `SG-DBX-005` (deny delete, replace or forget of `databricks_metastore*` unless flag `allow-metastore-destroy`) and `SG-DBX-006` (deny job permissions without a `CAN_MANAGE` or `IS_OWNER` entry, where a job permission has a known or unknown-until-apply `job_id`).

#### Scenario: Metastore destroy in prod
- **WHEN** a prod plan deletes a `databricks_metastore` and no flag is set
- **THEN** `SG-DBX-005` denies

### Requirement: Cloudflare rules
The Cloudflare pack SHALL implement `SG-CF-001` (proxied DNS record switched to DNS-only: deny in prod, warn elsewhere) and warnings `SG-CF-002` (Access application removed), `SG-CF-003` (firewall or ruleset removed or fewer ruleset rules), `SG-CF-004` (new tunnel ingress or tunnel route created or updated) and `SG-CF-005` (MX or apex record change), for v5 and v4 type names.

#### Scenario: Proxy turned off in prod
- **WHEN** a prod plan updates a `cloudflare_dns_record` from `proxied = true` to `false`
- **THEN** `SG-CF-001` denies

### Requirement: Messages name addresses only
Every policy message SHALL start with the rule id and name the resource address only, with no attribute values apart from the principal or privilege names a rule is about.

#### Scenario: DNS rule message
- **WHEN** `SG-CF-005` warns
- **THEN** the message contains the address but not the record's hostname or content
