# risk-gate Specification

## Purpose

The risk gate decides what the prod reviewer sees first: `ayasee changeset` classifies every change
of a plan into a risk tier using rule files, lists principals gaining or losing access, and renders a
values-free summary. It never blocks. Code: `src/ayasee/risk.py`, `src/ayasee/changeset.py`,
`rules/databricks-azure.yaml`, `rules/cloudflare.yaml`, `actions/risk`. Reference:
`docs/concepts/risk-gate.md`.

## Requirements

### Requirement: Tiers
The system SHALL classify each real change into `critical`, `high`, `medium` or `none`, exclude no-op, read and data-source changes from the change set, and order the summary critical, high, medium, then `none` collapsed under "other changes".

#### Scenario: Data source refresh
- **WHEN** the plan only reads data sources
- **THEN** the change set is empty

### Requirement: Rule matching
A rule SHALL match when the resource type matches (`*` matches any), any of the change's actions is in the rule's `actions` (`any` meaning every action except `no-op` and `read`), and every `attr_true`, `attr_changed` and `attr_in` condition holds. A replace SHALL count as delete plus create; the item's tier SHALL be the highest among matching rules; `generic-destroy` SHALL apply only to a delete or replace that matched no other rule; `moved`, `import` and `forget` SHALL be their own actions; `attr_true` SHALL read `after`, or `before` when there is no `after`.

#### Scenario: Forget of a metastore
- **WHEN** a `databricks_metastore` is forgotten
- **THEN** the change is `critical` through `dbx-workspace-destroy`

#### Scenario: Unmatched delete
- **WHEN** a resource type with no rule is deleted
- **THEN** it is `medium` through `generic-destroy`

### Requirement: Packaged rule packs
The system SHALL always load `rules/databricks-azure.yaml` then `rules/cloudflare.yaml`, covering Databricks/Azure destruction, access, isolation and unsafe flags, and Cloudflare Access, tunnel ingress, firewall and DNS (proxied, MX, apex) changes for both provider v5 and v4 type names.

#### Scenario: Grants update
- **WHEN** a `databricks_grants` resource is updated
- **THEN** it is `high` through `dbx-access`

### Requirement: Adopter rules can only raise a tier
Extra rule files (`--rules`, repeatable, or the `rules` input of `actions/risk`) SHALL be applied after the packaged files; they MAY add rules or replace a rule with the same id at the same or a higher tier; a rule that lowers a tier SHALL fail with "adopter rules may only raise a tier"; unknown keys, actions or tiers SHALL be errors.

#### Scenario: Lowering a tier
- **WHEN** an extra file redefines `dbx-access` with tier `medium`
- **THEN** `ayasee changeset` fails

### Requirement: Principal deltas
For `databricks_grants`, `databricks_grant`, `databricks_permissions`, `azurerm_role_assignment` and Cloudflare Access policies and groups, the change set SHALL list principals that gain and lose access, computed from before and after values; when a sensitive or unknown marker hides the principals, no delta SHALL be shown and the tier SHALL stay `high`.

#### Scenario: SELECT removed
- **WHEN** a grants update removes `SELECT` from `analysts`
- **THEN** the summary shows `lost: analysts SELECT` under High

### Requirement: Values-free, injection-safe summary
The summary SHALL read no values beyond principal, privilege and role names, SHALL neutralise backticks, line breaks and HTML in addresses and principal names, and SHALL open with a "Shared by every stage" line and mark each change **shared** when a unit in `shared_units` changes.

#### Scenario: Address with markup
- **WHEN** a resource address contains a backtick and `<script>`
- **THEN** the rendered summary contains neither as active markup

### Requirement: Root outputs count as changes
A change to a root output SHALL be a change-set item of kind `output` (address `output.<name>`, tier `none`, never the value), counted in the total but not in per-action resource counts, so that an output-only plan has changes.

#### Scenario: Output only
- **WHEN** only `output.endpoint` changes
- **THEN** the total is 1 and `has-changes` is true

### Requirement: Where the summary appears
The change-set summary SHALL be appended to the `prod-plan` job summary and posted in the pull-request sticky comment; notifications SHALL carry only the counts (critical, high, medium, total) and a link.

#### Scenario: prod_waiting card
- **WHEN** a prod plan has 1 critical change
- **THEN** the Teams card shows Critical 1 and no address
