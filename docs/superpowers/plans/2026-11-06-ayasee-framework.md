# ayasee: Actions-only IaC pipeline + cockpit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an open-source, server-less (GitHub Actions only) framework that promotes OpenTofu/Terragrunt changes from one trunk through `dev → uat → prod-plan → prod gate → prod` in one run, binds the prod apply to the approved encrypted plan, classifies Databricks/Azure risk at the gate, detects drift nightly, and publishes a read-only repo × environment cockpit built from committed JSON snapshots.

**Architecture:** An *execution plane*: reusable workflows (`iac-pr`, `iac-staged`, `iac-nightly`) built from thin composite actions, which call one Python CLI (`ayasee`) holding all the logic (plan parsing, risk, change set, plan binding, ledger, guard, notify). And an *observation plane*: `cockpit-collect` writes a versioned `snapshot.json` into a data repo, which a static TypeScript site (or a Markdown issue renderer on GHES) shows. Live state is the **backend ledger**; the Deployments API is only a projection.

**Tech Stack:** Python 3.12 + uv + pytest (CLI), OPA/Rego + conftest (policy pack), GitHub Actions YAML + actionlint + zizmor (workflows), OpenTofu ≥ 1.10 with `plan {}` encryption + Terragrunt (engine), Azure Blob (ledger + state), Vite + TypeScript + vitest (cockpit site), OpenTofu `tofu test` with mock providers (bootstrap modules).

**Spec:** the design decisions D1–D14 in [`docs/governance/decisions.md`](../../governance/decisions.md).

## Global Constraints

- Working name `ayasee` (package, CLI, action prefix); the name can change in one commit. Never use "Argo" in names; describe the cockpit as "Argo CD-style visibility".
- Clean-room repo: no third-party names, tickets, runner labels, secrets, brand tokens or data. Licence Apache-2.0 (D14).
- D1: the prod apply uses the **exact approved encrypted plan files**, checked by SHA-256 hash, engine version and provider lock hash. A stale or mismatched plan **stops the run and asks for re-plan + re-approval**; it is never re-planned silently. The subset check is not implemented.
- D1(b) fallback for plain Terraform: re-plan must be equal on `(address, actions)` **and** on a hash of the non-sensitive `after` values.
- D2: environments default to `prevent_self_review = true` and admins cannot bypass. `solo_mode: true` is opt-in and logs `NON-COMPLIANT: solo_mode disables independent approval (DORA RTS 2024/1774 Art. 17(1)(b))` on every run.
- D3: break-glass is a separate `prod-breakglass` environment with its own reviewers and a required incident reference; using it opens a post-implementation approval issue.
- D4: concurrency `queue: single` (default), never `cancel-in-progress` on apply jobs; guard rejects superseded SHAs.
- D5: the ledger in the backend is the source of truth for "what is live"; Deployments is written as a projection with `ref=main` only.
- D6: OpenTofu + Terragrunt first. Plain Terraform is a secondary profile using D1(b).
- D7: one Entra user-assigned managed identity per stage via OIDC (`azure/login`, `use_oidc`, `DATABRICKS_AUTH_TYPE=github-oidc-azure`). No stored cloud secrets.
- D8: one snapshot schema, pluggable renderers. Snapshots carry **counts, addresses and links only**: never plan values, never finding details.
- D9: the prod gate is `always` by default; `risky` is a per-repo option.
- D10: Trivy + Checkov on HCL and workflows, plus the Databricks Rego pack on plan JSON **inside the plan job**. Plan JSON and plan files never leave a job unencrypted; SARIF never contains values.
- D11: github.com-first CI, with a GHES compatibility suite. Use feature detection, not version assumptions.
- D13: ownership contract: metastore, catalogs, storage credentials, external locations and grants belong to Terraform; Bundles reference them by name.
- All third-party actions are pinned by commit SHA; callers pin `ayasee` by SHA.
- Plan JSON input is the OpenTofu/Terraform `show -json` format (`format_version` "1.x").

## Review Focus

1. **Sensitive or unknown values in plan JSON** (`after_sensitive`, `after_unknown`, `sensitive_values`): they must never reach the change set, the job summary, Teams cards, SARIF or the snapshot. Pinned by a leak test in Task 4 and Task 8.
2. **An approved run that is older than the current uat SHA** reaching the prod job after newer merges: it must be rejected as `superseded`, not applied. Pinned in Task 7 and the e2e `supersession` scenario in Task 17.
3. **A grants or permissions update that removes principals**: it must be tier HIGH, with the lost principals listed at the gate. Pinned in Task 3.
4. **An empty prod plan**: the gate is skipped and the ledger records prod as in sync at the new SHA, so drift checks don't keep planning an old SHA. Pinned in Task 6 and Task 12.
5. **`moved`, `import` and `forget` actions**: they must be classified on their own (not as create/delete), and `forget` of a critical type counts as critical. Pinned in Task 2 and Task 3.

---

## Top open question (answer before Phase 3)

**Is the target estate on GitHub Enterprise Cloud or GHES (and which version)?** It decides cockpit hosting (Tasks 19–21), `queue: max`, workflow lockfiles, `deployment: false`, and the OIDC `sub` format. The plan builds github.com-first and gates GHES-only behaviour behind feature detection (Task 24).

## File Structure

```
ayasee/
├── pyproject.toml                    # uv project, CLI entry point `ayasee`
├── src/ayasee/
│   ├── cli.py                        # argparse subcommands, one per module below
│   ├── config.py                     # .ayasee.yaml model + defaults + validation
│   ├── planjson.py                   # plan JSON → ResourceChange, Action
│   ├── risk.py                       # Tier, Rule, classify(), principal_delta()
│   ├── changeset.py                  # sanitized change set + Markdown summary
│   ├── binding.py                    # plan manifest: hashes, engine, lock; verify()
│   ├── ledger.py                     # LedgerRecord + stores (azure-blob, git-branch, file)
│   ├── guard.py                      # predecessor, supersession, stop-the-line
│   ├── notify.py                     # Teams Adaptive Card builder + poster
│   ├── github.py                     # minimal REST client (httpx), App/installation auth
│   └── cockpit/collect.py            # build Snapshot from ledger + GitHub
│   └── cockpit/render_md.py          # snapshot → Markdown (issue / job summary renderer)
├── rules/databricks-azure.yaml       # risk tiers (data, not code)
├── policy/databricks/*.rego          # deny/warn policies on plan JSON + *_test.rego
├── schema/snapshot.v1.json           # JSON Schema: the cockpit's public contract
├── actions/{plan,apply,risk,guard,ledger,notify,setup}/action.yml
├── .github/workflows/{iac-pr,iac-staged,iac-nightly,cockpit-collect}.yml   # workflow_call
├── .github/workflows/ci.yml          # self-test: lint, unit, opa, actionlint, zizmor
├── modules/github-environments/      # OpenTofu: envs, reviewers, branch policy
├── modules/azure-stage-identity/     # OpenTofu: UAMI + federated credential per stage
├── cockpit-site/                     # Vite + TS: matrix, drawer, history
├── examples/databricks-azure/        # reference adopter repo (Terragrunt units)
├── e2e/fixture/                      # cloud-free Terragrunt units for sandbox runs
├── e2e/scenarios/*.py                # scenario drivers against a sandbox repo
├── docs/                             # concepts, compliance mapping, comparison, migration
└── tests/                            # pytest, mirrors src/
```

---

## Phase 0 — Governance and repository

### Task 0: Provenance and licence statement (non-code)

**Files:**
- Create: `docs/governance/PROVENANCE.md`

- [ ] **Step 1:** Write `PROVENANCE.md` stating that the work is built from public sources only, with no third-party data, and that it is licensed under Apache-2.0 (D14).
- [ ] **Step 2:** Gate: the release checks that `PROVENANCE.md`, `pyproject.toml`, `LICENSE` and `NOTICE` agree (`scripts/check-provenance.sh`). Commit.

### Task 1: Repository scaffold and self-test CI

**Files:**
- Create: `pyproject.toml`, `src/ayasee/__init__.py`, `src/ayasee/cli.py`, `tests/test_cli.py`, `.github/workflows/ci.yml`, `LICENSE`, `README.md`, `.gitignore` (includes `.superpowers/`), `renovate.json`

**Interfaces:**
- Produces: console script `ayasee` with `ayasee --version` printing `ayasee <version>`; subcommands registered by later tasks via `cli.register(name, fn)`.

- [ ] **Step 1: Write the failing test** `test_version_prints_package_version`: runs `main(["--version"])`, asserts stdout == `f"ayasee {ayasee.__version__}\n"`.
- [ ] **Step 2:** Run `uv run pytest tests/test_cli.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement `main(argv: list[str] | None = None) -> int` in `cli.py` with argparse and a subparser registry.
- [ ] **Step 4:** Run the test. Expected: PASS.
- [ ] **Step 5:** Write `ci.yml` with these jobs: `uv run ruff check`, `uv run pytest`, `opa test policy/`, `actionlint`, `zizmor .github/ actions/`, and `npm --prefix cockpit-site test`. All actions pinned by SHA. Push a branch and confirm CI is green.
- [ ] **Step 6:** Commit `chore: scaffold ayasee package and CI`.

---

## Phase 1 — Core library (pure Python, no cloud)

### Task 2: Plan JSON model

**Files:**
- Create: `src/ayasee/planjson.py`, `tests/test_planjson.py`, `tests/fixtures/plans/*.json` (hand-written minimal plans: create, update, delete, replace both orders, read, no-op, import, moved, forget, sensitive, unknown)

**Interfaces:**
- Produces:
  - `class Action(StrEnum)`: `NOOP, CREATE, UPDATE, DELETE, REPLACE, READ, IMPORT, MOVE, FORGET`
  - `@dataclass(frozen=True) class ResourceChange`: `address: str, type: str, provider: str, mode: str, actions: tuple[Action, ...], before: dict | None, after: dict | None, after_unknown: dict, before_sensitive: dict | bool, after_sensitive: dict | bool, previous_address: str | None`
  - `def load_plan(path: Path) -> list[ResourceChange]`
  - `def normalize(raw_actions: list[str], importing: bool, previous_address: str | None) -> tuple[Action, ...]`

- [ ] **Step 1: Write failing tests:**
  - `test_replace_both_orders`: `normalize(["delete","create"],False,None) == (Action.REPLACE,)` and the same for `["create","delete"]`.
  - `test_import_with_update`: `normalize(["update"],True,None) == (Action.IMPORT, Action.UPDATE)`.
  - `test_moved_noop`: `normalize(["no-op"],False,"a.old") == (Action.MOVE,)`.
  - `test_forget`: `normalize(["forget"],False,None) == (Action.FORGET,)`.
  - `test_load_plan_skips_data_sources_read`: a fixture with one `data.` read and one managed create gives 2 changes, and the data one has `mode == "data"`.
  - `test_unknown_format_version_raises`: `format_version: "2.0"` raises `PlanFormatError`.
- [ ] **Step 2:** Run `uv run pytest tests/test_planjson.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `planjson.py`. Read `resource_changes[*].change.{actions,importing,before,after,after_unknown,before_sensitive,after_sensitive}` and `previous_address`. Accept `format_version` major `1` only.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(planjson): parse plan JSON into normalized changes`.

### Task 3: Risk classifier with Databricks/Azure tiers and principal deltas

**Files:**
- Create: `src/ayasee/risk.py`, `rules/databricks-azure.yaml`, `tests/test_risk.py`, `tests/fixtures/plans/grants_*.json`, `permissions_*.json`, `role_assignment_*.json`, `workspace_replace.json`

**Interfaces:**
- Consumes: `ResourceChange`, `Action` (Task 2).
- Produces:
  - `class Tier(IntEnum)`: `NONE=0, MEDIUM=1, HIGH=2, CRITICAL=3`
  - `@dataclass(frozen=True) class Rule`: `id: str, tier: Tier, types: tuple[str, ...], actions: frozenset[Action], attr_true: tuple[str, ...] = (), attr_changed: tuple[str, ...] = ()` (match only if `before[attr] != after[attr]`)
  - `@dataclass(frozen=True) class PrincipalDelta`: `gained: dict[str, tuple[str, ...]], lost: dict[str, tuple[str, ...]]` (principal → privileges/levels/roles)
  - `@dataclass(frozen=True) class RiskItem`: `unit: str, address: str, actions: tuple[Action, ...], tier: Tier, rule_ids: tuple[str, ...], principal_delta: PrincipalDelta | None`
  - `def load_rules(*paths: Path) -> list[Rule]` (later files add rules; an adopter file may only raise a tier, never lower one)
  - `def classify(unit: str, changes: list[ResourceChange], rules: list[Rule]) -> list[RiskItem]` (sorted by tier desc, then address)
  - `def principal_delta(rc: ResourceChange) -> PrincipalDelta | None`

Rules to encode in `rules/databricks-azure.yaml` (verbatim from the spec):
- CRITICAL `dbx-workspace-destroy`: `azurerm_databricks_workspace`, `databricks_metastore`, `databricks_metastore_assignment` on DELETE/REPLACE/FORGET.
- CRITICAL `dbx-storage-destroy`: `databricks_storage_credential`, `databricks_external_location` on DELETE/REPLACE/FORGET.
- CRITICAL `force-destroy`: any type on DELETE/REPLACE with `attr_true: [force_destroy]`.
- HIGH `dbx-access`: `databricks_grants`, `databricks_grant`, `databricks_permissions`, `databricks_permission_assignment`, `databricks_mws_permission_assignment`, `azurerm_role_assignment` on any non-NOOP action.
- HIGH `dbx-isolation`: `databricks_workspace_binding`, `databricks_catalog` with `attr_changed: [isolation_mode]`, plus network types `azurerm_private_endpoint`, `azurerm_subnet`, `azurerm_network_security_group`, and `databricks_service_principal_federation_policy`, on any non-NOOP action.
- MEDIUM `dbx-compute-policy`: `databricks_cluster_policy`, `databricks_sql_endpoint` on any non-NOOP action; any type with `attr_true: [skip_validation]` or `[force_update]`.
- Default for any other DELETE/REPLACE: MEDIUM `generic-destroy`.

- [ ] **Step 1: Write failing tests:**
  - `test_workspace_replace_is_critical`: `classify("ws", load_plan(workspace_replace.json), rules)[0].tier == Tier.CRITICAL` and `"dbx-workspace-destroy" in rule_ids`.
  - `test_grants_update_lists_lost_principal` (Review Focus 3): before has grants `data_engineers:[USE_CATALOG,SELECT]` and `analysts:[SELECT]`, after drops `analysts`. Assert tier HIGH and `principal_delta.lost == {"analysts": ("SELECT",)}`, `gained == {}`.
  - `test_permissions_level_change`: `CAN_VIEW→CAN_MANAGE` for `group_name=ops` appears in both `lost` and `gained`.
  - `test_role_assignment_create_gained`: `principal_delta.gained == {"<principal_id>": ("Contributor",)}`.
  - `test_forget_metastore_is_critical` (Review Focus 5).
  - `test_moved_only_is_none`: a MOVE with no other action gives `Tier.NONE`.
  - `test_adopter_rules_cannot_lower_tier`: loading an extra file that sets `dbx-access` to MEDIUM raises `RuleError`.
  - `test_sensitive_principal_omitted`: when `after_sensitive` marks the grants block sensitive, `principal_delta` is `None` and the item carries tier HIGH with `rule_ids` containing `dbx-access`.
- [ ] **Step 2:** Run `uv run pytest tests/test_risk.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `risk.py`. Grant blocks are `grant[*].{principal, privileges}`. Permissions blocks are `access_control[*].{user_name|group_name|service_principal_name, permission_level}`. Role assignments are `{principal_id, role_definition_name|role_definition_id}`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(risk): tiered Databricks/Azure classifier with principal deltas`.

### Task 4: Sanitized change set and gate summary

**Files:**
- Create: `src/ayasee/changeset.py`, `tests/test_changeset.py`

**Interfaces:**
- Consumes: `RiskItem`, `Tier` (Task 3).
- Produces:
  - `def build_changeset(sha: str, env: str, items: list[RiskItem]) -> dict` with shape `{"schema": "ayasee.changeset/v1", "sha", "env", "counts": {"create","update","delete","replace","import","move","forget"}, "risk": {"critical","high","medium"}, "items": [{"unit","address","actions":[...],"tier","rules":[...],"principals":{"gained":{...},"lost":{...}}|null}]}`
  - `def render_summary(cs: dict) -> str`: Markdown, critical then high then medium, then a collapsed `<details>` with the rest. First line: `### Prod gate: {critical} critical · {high} high · {medium} medium · {total} changes`.
  - CLI: `ayasee changeset --plan-json DIR --unit-map FILE --sha SHA --env ENV --out changeset.json --summary $GITHUB_STEP_SUMMARY`

- [ ] **Step 1: Write failing tests:**
  - `test_counts_and_order`: a fixture with 1 critical, 2 high and 3 untiered items. Assert counts and that `items[0]["tier"] == "critical"`.
  - `test_no_values_leak` (Review Focus 1): a fixture whose `after` contains `"token": "s3cr3t-VALUE"` and whose `after_sensitive` marks it. Assert `"s3cr3t-VALUE" not in json.dumps(cs)` and `not in render_summary(cs)`. Repeat with a value that is **not** marked sensitive (`"comment": "plain-VALUE"`): it must also be absent, because only addresses, actions and principals are ever emitted.
  - `test_summary_header`: exact first line for counts 1/2/0/6.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement with an allow-list serializer (only the fields above).
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(changeset): sanitized change set and gate summary`.

### Task 5: Plan binding manifest (D1)

**Files:**
- Create: `src/ayasee/binding.py`, `tests/test_binding.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Manifest`: `sha: str, env: str, engine: str, engine_version: str, units: dict[str, UnitBinding]`, where `UnitBinding(plan_sha256: str, lock_sha256: str, values_sha256: str | None)`
  - `def make_manifest(sha, env, engine, engine_version, plan_files: dict[str, Path], lock_files: dict[str, Path], plan_json: dict[str, Path] | None = None) -> Manifest` (`values_sha256` is only set in D1(b) mode: a hash of `after` with sensitive and unknown paths removed, canonical JSON)
  - `def verify(manifest: Manifest, *, sha, engine_version, plan_files, lock_files) -> None`, raising `BindingError(reason: Literal["sha","engine","plan_hash","lock_hash","unit_set","values"], unit: str | None)`
  - `def verify_replan(manifest, replan_json: dict[str, Path]) -> None` (D1(b): `(address, actions)` set equal **and** `values_sha256` equal)
  - `Manifest.to_json() / Manifest.from_json()`
  - CLI: `ayasee binding make|verify|verify-replan ...`

- [ ] **Step 1: Write failing tests:**
  - `test_roundtrip_ok`.
  - `test_plan_byte_change_fails`: flipping one byte gives `reason == "plan_hash"` with the unit name.
  - `test_engine_drift_fails`: `"1.10.2"` vs `"1.10.3"` gives `"engine"`.
  - `test_lock_change_fails`.
  - `test_missing_unit_fails`: `"unit_set"`.
  - `test_replan_value_change_fails`: same actions, different non-sensitive `after.comment` gives `BindingError` with `reason == "values"`.
  - `test_replan_sensitive_change_ignored`: a change only under a sensitive path passes (documented limit).
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement with `hashlib.sha256` over the file bytes and canonical JSON (`sort_keys=True, separators=(",",":")`).
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(binding): plan manifest and verification`.

### Task 6: Ledger (D5)

**Files:**
- Create: `src/ayasee/ledger.py`, `tests/test_ledger.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class UnitResult`: `status: Literal["applied","no_changes","failed","skipped"], converged: bool`
  - `@dataclass(frozen=True) class LedgerRecord`: `repo: str, env: str, sha: str, run_id: int, run_attempt: int, units: dict[str, UnitResult], converged: bool, approver: str | None, breakglass_incident: str | None, recorded_at: str` (RFC 3339 UTC)
  - `class LedgerStore(Protocol)`: `def current(self, repo: str, env: str) -> LedgerRecord | None`, `def append(self, rec: LedgerRecord) -> None`, `def history(self, repo: str, env: str, limit: int = 50) -> list[LedgerRecord]`
  - Implementations: `FileStore(root: Path)`, `GitBranchStore(repo_dir: Path, branch: str = "ayasee-ledger")`, `AzureBlobStore(account_url: str, container: str, prefix: str = "ayasee-ledger")` (DefaultAzureCredential)
  - Layout for every store: `{prefix}/{repo}/{env}/history/{recorded_at}-{sha[:12]}.json` plus `{prefix}/{repo}/{env}/current.json`
  - `def record_from_run(...) -> LedgerRecord` (`converged` is true iff every unit's `converged` is true)
  - CLI: `ayasee ledger write|show ...`; `--mirror-deployments` also posts a GitHub deployment + status with `ref=main` and `environment=env`

- [ ] **Step 1: Write failing tests** (run against `FileStore` and `GitBranchStore` with a temp git repo, via `pytest.mark.parametrize`):
  - `test_append_then_current`.
  - `test_history_newest_first_and_limit`.
  - `test_empty_plan_records_in_sync` (Review Focus 4): `record_from_run(units={"a": UnitResult("no_changes", True)})` gives `converged is True`, and `current().sha` is the new SHA.
  - `test_failed_unit_not_converged`.
  - `test_current_none_when_absent` (first run / bootstrap).
  - Azure store: `test_azure_blob_layout` with `azure.storage.blob` mocked, asserting the blob names.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. Writes are append-history first, then overwrite `current.json`. The Azure store sets blob index tag `ayasee-sha=<sha>`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(ledger): backend ledger with file, git-branch and Azure Blob stores`.

### Task 7: Guard

**Files:**
- Create: `src/ayasee/guard.py`, `tests/test_guard.py`

**Interfaces:**
- Consumes: `LedgerStore`, `LedgerRecord` (Task 6).
- Produces:
  - `@dataclass(frozen=True) class GuardResult`: `ok: bool, reasons: tuple[str, ...]` (reason codes: `predecessor_missing`, `predecessor_not_converged`, `superseded`, `stop_the_line`)
  - `class IssueQuery(Protocol)`: `def open_stop_the_line(self, repo: str, env: str) -> list[int]` (issues labelled `ayasee:stop-the-line` and `env:<env>`)
  - `def check(repo: str, stage: str, sha: str, order: list[str], store: LedgerStore, issues: IssueQuery) -> GuardResult`
  - CLI: `ayasee guard --stage prod --sha $SHA` (exit 1 on failure, reasons printed and added to the step summary)

Rules: for stage at index i > 0, the predecessor `order[i-1]` must have `current.sha == sha` and `converged`. Otherwise the reason is `predecessor_missing` / `predecessor_not_converged`, or `superseded` when the predecessor converged at a **different** SHA. For `prod` and `prod-plan` only, an open stop-the-line issue for `prod`, or any open apply-failure issue of an earlier stage, gives `stop_the_line`. `prod-plan` is treated as a stage that writes no ledger record; its predecessor check uses `uat`.

- [ ] **Step 1: Write failing tests:**
  - `test_dev_always_ok_without_issues`.
  - `test_prod_superseded` (Review Focus 2): uat current SHA `bbb` converged, guard prod at `aaa`, gives `reasons == ("superseded",)`.
  - `test_prod_ok_same_sha`.
  - `test_uat_red_blocks_prod`.
  - `test_stop_the_line_blocks_prod_not_dev`.
  - `test_prod_plan_uses_uat_as_predecessor`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(guard): predecessor, supersession and stop-the-line checks`.

### Task 8: Teams notifications

**Files:**
- Create: `src/ayasee/notify.py`, `tests/test_notify.py`

**Interfaces:**
- Consumes: the change set dict (Task 4).
- Produces:
  - `class Event(StrEnum)`: `PROD_WAITING, PROD_DEPLOYED, PROD_REJECTED, PROD_HELD, PROD_FAILED, STAGE_FAILED, DRIFT_FOUND, DRIFT_CLEARED, NEW_FINDINGS`
  - `def build_card(event: Event, *, repo: str, env: str, sha: str, run_url: str, counts: dict | None = None, approver: str | None = None) -> dict` (an Adaptive Card 1.5 wrapped in the Teams Workflows message envelope)
  - `def post(webhook_url: str, card: dict, timeout: float = 10.0) -> bool`: returns `False` and logs `::warning::` on any failure, never raises
  - CLI: `ayasee notify --event prod_waiting --changeset changeset.json ...` (webhook from env `AYASEE_TEAMS_WEBHOOK`; if unset, it prints a notice and exits 0)

- [ ] **Step 1: Write failing tests:**
  - `test_prod_waiting_has_counts_and_link`: the facts include `Critical: 1` and the action URL equals `run_url`.
  - `test_card_never_contains_values` (Review Focus 1): build from the Task 4 leak fixture and assert neither secret string appears in `json.dumps(card)`.
  - `test_post_failure_returns_false` (`respx` 500).
  - `test_no_webhook_is_noop`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement with `httpx`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(notify): Teams cards with counts and links only`.

### Task 9: Configuration file

**Files:**
- Create: `src/ayasee/config.py`, `tests/test_config.py`, `docs/reference/ayasee-yaml.md`

**Interfaces:**
- Produces: `@dataclass class Config`. Fields: `stages: list[str] = ["dev","uat","prod"]`, `engine: Literal["opentofu","terraform"] = "opentofu"`, `terragrunt: bool = True`, `units_root: str = "stages"`, `ledger: LedgerConfig`, `gate: Literal["always","risky"] = "always"`, `solo_mode: bool = False`, `extra_rules: list[str] = []`, `drift_noise: dict[str, list[str]] = {}` (resource type → ignored attribute paths), `notify: NotifyConfig`. Plus `def load_config(path: Path = Path(".ayasee.yaml")) -> Config`.
- `load_config` emits the exact `NON-COMPLIANT…` line from Global Constraints to stderr when `solo_mode` is true.

- [ ] **Step 1: Write failing tests:** `test_defaults_when_file_missing`, `test_solo_mode_warns_exact_text`, `test_unknown_key_rejected`, `test_terraform_engine_selects_d1b` (`cfg.binding_mode == "replan"`; OpenTofu gives `"encrypted_plan"`).
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement and document every key in `ayasee-yaml.md`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(config): .ayasee.yaml with compliant defaults`.

---

## Phase 2 — Databricks policy pack

### Task 10: Rego pack on plan JSON

**Files:**
- Create: `policy/databricks/grants.rego`, `policy/databricks/workspace.rego`, `policy/databricks/storage.rego`, `policy/databricks/*_test.rego`, `docs/reference/policy-pack.md`

**Interfaces:**
- Produces: conftest namespace `ayasee.databricks`, with `deny[msg]` and `warn[msg]` rules evaluated on `tofu show -json` output. Messages are prefixed with the rule id (`SG-DBX-001` …).

Policies (deny unless noted):
- `SG-DBX-001` a `databricks_grants` or `databricks_grant` `after` contains the principal `account users` with `ALL_PRIVILEGES`.
- `SG-DBX-002` `databricks_catalog` without `isolation_mode = "ISOLATED"` in a stage labelled prod (input `stage` passed via `--data`).
- `SG-DBX-003` `azurerm_databricks_workspace` with `public_network_access_enabled = true` in prod (warn in dev).
- `SG-DBX-004` `databricks_external_location` or `databricks_storage_credential` with `skip_validation = true` (warn).
- `SG-DBX-005` any DELETE of `databricks_metastore*` without the `allow-metastore-destroy` input flag.
- `SG-DBX-006` `databricks_permissions` that would leave a job without any `CAN_MANAGE` holder.

- [ ] **Step 1: Write failing tests:** one `test_<rule>_denies` and one `test_<rule>_allows` per rule, with inline plan fragments.
- [ ] **Step 2:** Run `opa test policy/ -v`. Expected: FAIL.
- [ ] **Step 3:** Implement the rules.
- [ ] **Step 4:** Run `opa test policy/ -v`. Expected: all PASS.
- [ ] **Step 5:** Write `policy-pack.md` with an honest coverage table: Trivy has 0 Databricks checks, Checkov has 2 (CKV_AZURE_158, CKV2_AZURE_48), and this pack has 6. Commit `feat(policy): Databricks Rego pack on plan JSON`.

---

## Phase 3 — Actions and workflows

### Task 11: Composite actions (setup, plan, apply, risk, guard, ledger, notify)

**Files:**
- Create: `actions/setup/action.yml` (installs pinned OpenTofu, Terragrunt, uv and `ayasee` from `${{ github.action_path }}/../..`, verifying each binary's checksum), `actions/plan/action.yml`, `actions/apply/action.yml`, `actions/risk/action.yml`, `actions/guard/action.yml`, `actions/ledger/action.yml`, `actions/notify/action.yml`, `actions/plan/encryption.tofu.tmpl`
- Test: `tests/actions/test_action_metadata.py`

**Interfaces:**
- `plan` inputs: `env`, `units-root`, `encrypt` (bool, default true), `key-vault-key-id`. Outputs: `has-changes`, `manifest-path`, `plan-dir`. It plans each unit in Terragrunt dependency order (`terragrunt run --all plan -out=tfplan`). With `encrypt` on, it injects the OpenTofu `encryption { key_provider "azure_vault" … method "aes_gcm" … plan { method = … enforced = true } }` block through `TF_ENCRYPTION`. It writes `tofu show -json` into the job's temp dir only (never uploaded), then calls `ayasee binding make`.
- `apply` inputs: `env`, `plan-artifact`, `manifest-path`, `binding-mode`. It runs `ayasee binding verify` first, then applies per unit in dependency order. After applying, it re-plans with `-detailed-exitcode` (converge check: exit 0 is required) and writes per-unit `UnitResult` JSON.
- `risk`, `guard`, `ledger`, `notify`: thin wrappers over the CLI subcommands from Tasks 3–8.

- [ ] **Step 1: Write failing tests:** `test_all_actions_pin_uses_by_sha` (parse every `action.yml`; every `uses:` matches `@[0-9a-f]{40}`), `test_plan_never_uploads_json` (no `upload-artifact` step references `*.json` in `actions/plan`), and `test_inputs_documented` (every input has a `description`).
- [ ] **Step 2:** Run `uv run pytest tests/actions -v`. Expected: FAIL.
- [ ] **Step 3:** Write the action YAML files.
- [ ] **Step 4:** Run the tests, `actionlint` and `zizmor actions/`. Expected: PASS, no findings.
- [ ] **Step 5:** Commit `feat(actions): composite actions over the ayasee CLI`.

### Task 12: `iac-staged` reusable workflow

**Files:**
- Create: `.github/workflows/iac-staged.yml`, `docs/concepts/staged-run.md`
- Test: `tests/workflows/test_iac_staged.py`

**Interfaces:**
- `on: workflow_call`. Inputs: `start-stage` (default `dev`), `config-path`. Secrets: none required (OIDC). An optional `teams-webhook` is passed explicitly, not with `inherit`.
- Jobs: `dev`, `uat`, `prod-plan`, `prod`. Each has `environment:` of the same name, `permissions: {id-token: write, contents: read, deployments: write, issues: write}`, `concurrency: {group: ayasee-${{ github.repository }}-<env>, cancel-in-progress: false}`, and `if: github.ref == 'refs/heads/main'`. A job is skipped when its stage comes before `start-stage`.
- `dev` and `uat` run: guard → plan → apply (same job, `binding-mode: same-job`) → ledger write → notify on failure (+ a stop-the-line issue).
- `prod-plan` runs: guard → plan (encrypted) → risk + changeset → a Rego pack + Trivy plan scan (in-job) → upload the encrypted plan artifact (retention 7 days) + manifest. Outputs: `has-changes`, `critical`, `high`. It notifies `prod_waiting` when there are changes.
- `prod`: `if: needs.prod-plan.outputs.has-changes == 'true'`, gated by the `prod` environment. Steps: guard (re-checks supersession **at apply time**) → download → apply with verify → ledger (approver read from the approvals API) → notify result.
- An empty prod plan: a final `prod-in-sync` job writes the ledger for `prod` with `no_changes` at the SHA (Review Focus 4).

- [ ] **Step 1: Write failing tests** that parse the YAML:
  - `test_no_cancel_in_progress_on_any_job`.
  - `test_prod_needs_prod_plan_and_is_conditional`.
  - `test_every_job_has_environment_and_main_ref_guard`.
  - `test_no_secrets_inherit`.
  - `test_empty_plan_path_writes_ledger` (the `prod-in-sync` job exists with the inverse condition).
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Write the workflow.
- [ ] **Step 4:** Run the tests, `actionlint` and `zizmor`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(workflows): iac-staged one-run promotion`.

### Task 13: `iac-pr` reusable workflow

**Files:**
- Create: `.github/workflows/iac-pr.yml`
- Test: `tests/workflows/test_iac_pr.py`

**Interfaces:**
- `on: workflow_call`, called on `pull_request`. Jobs:
  - `checks`: fmt, validate, conftest + the pack, Trivy config + Checkov with `--baseline .ayasee/checkov.baseline` and SARIF upload.
  - `plan-dev`: environment `dev-plan`, a read-only plan identity, with `deployment: false` where the platform supports it (feature-detected). It plans changed units only and posts a sticky PR comment with `render_summary`.
- It never applies. It never reads uat or prod credentials.

- [ ] **Step 1: Write failing tests:** `test_pr_never_runs_apply` (no step uses `actions/apply`), `test_pr_plan_uses_dev_plan_environment_only`, and `test_sarif_has_no_values` (run the CLI's SARIF post-filter on a fixture; assert no `after` values).
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Write the workflow, plus `ayasee sarif-scrub` (a small CLI that drops `message` fields matching value patterns).
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(workflows): iac-pr checks and dev plan`.

### Task 14: `iac-nightly` drift and re-scan

**Files:**
- Create: `.github/workflows/iac-nightly.yml`, `src/ayasee/drift.py`, `tests/test_drift.py`

**Interfaces:**
- `drift.py`: `def evaluate(env: str, exit_code: int, changes: list[ResourceChange], noise: dict[str, list[str]]) -> DriftResult(drifted: bool, addresses: tuple[str, ...])`. It ignores changes limited to the noise paths. `def sync_issue(gh, repo, env, result) -> int | None` opens or updates one issue per env (labels `ayasee:drift`, `env:<env>`, plus `ayasee:stop-the-line` when env is `prod`) and closes it when the drift is cleared.
- Workflow: a cron trigger with a matrix over the stages. It checks out `ledger.current(env).sha`, not `main`. It takes the state lock (normal plan) and uses `concurrency` group `ayasee-drift-<env>`, separate from apply. It skips the env if a stop-the-line issue for an apply failure is open (HCP rule). It re-scans `main` with Trivy/Checkov and posts `new_findings` for new HIGH/CRITICAL findings.

- [ ] **Step 1: Write failing tests:** `test_exit_0_no_drift`, `test_noise_only_is_no_drift` (`databricks_permissions` with the noise path `access_control`), `test_prod_drift_adds_stop_the_line_label`, `test_cleared_closes_issue` (fake gh).
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement `drift.py` and the workflow.
- [ ] **Step 4:** Run the tests and `actionlint`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(drift): nightly drift at live SHA with one issue per env`.

### Task 15: Break-glass and compliance mode (D2, D3)

**Files:**
- Create: `.github/workflows/iac-breakglass.yml`, `docs/compliance/dora-art17.md`
- Modify: `src/ayasee/ledger.py` (the `breakglass_incident` field is already defined; this adds the CLI flag)
- Test: `tests/workflows/test_breakglass.py`

**Interfaces:**
- `workflow_call` + `workflow_dispatch` inputs: `incident` (required, pattern `^[A-Z]+-\d+$|^INC\d+$`), `units` (list), `sha`. Environment: `prod-breakglass`. Steps: plan → apply (same job) → ledger with `breakglass_incident` → open an `ayasee:post-approval` issue with a checklist for after-the-fact approval (Art. 17(1)(g)).

- [ ] **Step 1: Write failing tests:** `test_incident_required`, `test_uses_breakglass_environment`, and `test_opens_post_approval_issue_step`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Write the workflow. Write `dora-art17.md`, mapping each Art. 17 point to a framework control: independence goes to the environment reviewers with prevent-self-review and no admin bypass; emergency changes go to break-glass; the record goes to the ledger plus the approvals API.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(compliance): break-glass path and DORA Art. 17 mapping`.

### Task 16: Bootstrap modules (environments + identities)

**Files:**
- Create: `modules/github-environments/{main.tf,variables.tf,outputs.tf,tests/defaults.tftest.hcl}`, `modules/azure-stage-identity/{main.tf,variables.tf,outputs.tf,tests/subjects.tftest.hcl}`

**Interfaces:**
- `github-environments`: input `repository`, `reviewers` (teams/users), `stages`. It creates `dev-plan`, each stage, `prod-plan` and `prod-breakglass`, with `prevent_self_review = true`, `can_admins_bypass = false`, and deployment branch policy `main` (except `dev-plan`: all branches).
- `azure-stage-identity`: input `repository`, `stage`, `resource_group`, `issuer` (default `https://token.actions.githubusercontent.com`; set to the GHES issuer on GHES). It creates a UAMI and a federated credential with subject `repo:<owner>/<repo>:environment:<stage>`. Outputs `client_id`, `principal_id`.

- [ ] **Step 1: Write failing `tofu test` files** with `mock_provider`: `run "prod_is_independent"` asserts `github_repository_environment.this["prod"].prevent_self_review == true` and `can_admins_bypass == false`; `run "subject_format"` asserts the subject string exactly.
- [ ] **Step 2:** Run `tofu test` in each module. Expected: FAIL.
- [ ] **Step 3:** Implement the modules.
- [ ] **Step 4:** Run `tofu test`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(modules): environment and stage-identity bootstrap`.

### Task 17: End-to-end sandbox scenarios

**Files:**
- Create: `e2e/fixture/stages/{dev,uat,prod}/{base,grants}/terragrunt.hcl` (units built on `terraform_data` and `random_*`, plus a fake `databricks_grants`-shaped `terraform_data` input so plan JSON exercises the risk rules), `e2e/fixture/.ayasee.yaml` (ledger `git-branch`), `e2e/scenarios/{valid,scan_fail,pr_plan_fail,dev_apply_fail,gate_reject,drift,supersession,stale_plan,empty_prod}.py`, `e2e/README.md`, `.github/workflows/e2e.yml` (manual + nightly on the ayasee repo)

**Interfaces:**
- Each scenario: `def run(sandbox: Sandbox) -> None`. `Sandbox` pushes a commit to the sandbox repo `ayasee-sandbox`, waits for runs through the REST API, approves or rejects with a second bot identity, and asserts on the ledger, issues and run conclusions.

- [ ] **Step 1:** Write the scenarios with these assertions:
  - `valid`: all four stages succeed, and the ledger shows dev, uat and prod at the merge SHA.
  - `scan_fail`: the PR check fails and no staged run starts.
  - `dev_apply_fail`: uat and prod are skipped, and a stop-the-line issue is open.
  - `gate_reject`: prod is not applied and a `prod_rejected` card is posted (captured by a webhook echo).
  - `drift`: a manual state edit leads the nightly run to open a drift issue with the stop-the-line label.
  - `supersession` (Review Focus 2): merge A, then merge B while A waits at the gate, then approve A. A's prod job must fail at guard with `superseded`.
  - `stale_plan`: change state between prod-plan and approval. Apply must fail with a binding/stale error and never apply.
  - `empty_prod` (Review Focus 4): the gate is skipped and the prod ledger SHA moves.
- [ ] **Step 2:** Run `uv run python -m e2e.run --all` against the sandbox. Expected: all scenarios PASS. Record the run URLs in `e2e/README.md`.
- [ ] **Step 3:** Commit `test(e2e): sandbox scenarios for the staged run`.

---

## Phase 4 — Cockpit

### Task 18: Snapshot schema and collector

**Files:**
- Create: `schema/snapshot.v1.json`, `src/ayasee/github.py`, `src/ayasee/cockpit/collect.py`, `tests/cockpit/test_collect.py`, `tests/cockpit/fixtures/*.json` (recorded REST responses)

**Interfaces:**
- Snapshot v1: `{"schema":"ayasee.snapshot/v1","generated_at","repos":[{"repo","main_sha","envs":[{"env","live_sha","live_at","converged","pending_commits":int,"pending_compare_url","gate":{"waiting":bool,"since":str|null,"run_url":str|null},"drift":{"open":bool,"since":str|null,"issue_url":str|null,"resources":int},"findings":{"critical":int,"high":int,"url"},"health":{"last_result":"applied|failed|no_changes|unknown","stop_the_line":bool}}]}]}`.
- `github.py`: `class GitHub(base_url: str, token: str)` with `compare(repo, base, head) -> int`, `pending_deployment_runs(repo) -> list[dict]`, `issues(repo, labels) -> list[dict]`, `code_scanning_counts(repo) -> dict`, `main_sha(repo) -> str`, plus `def app_installation_token(app_id, private_key, base_url) -> str`.
- `collect(repos: list[str], gh: GitHub, store: LedgerStore, stages: list[str]) -> dict`. Pending commits are counted for each env against its predecessor's live SHA (dev against `main`).
- CLI: `ayasee cockpit collect --repos-file repos.txt --out snapshot.json`.

- [ ] **Step 1: Write failing tests:**
  - `test_snapshot_validates_against_schema` (`jsonschema`).
  - `test_pending_counts_chain`: main 5 ahead of dev, and dev 2 ahead of uat, give dev 5 and uat 2.
  - `test_gate_waiting_from_pending_runs`.
  - `test_unknown_when_no_ledger`: `live_sha` null and `health.last_result == "unknown"`.
  - `test_snapshot_has_no_finding_details` (D8): only integers under `findings` besides the url.
  - `test_ghes_base_url_used`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement with `httpx`, using ETag-conditional requests to save rate limit.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(cockpit): snapshot v1 schema and collector`.

### Task 19: `cockpit-collect` workflow and data repo

**Files:**
- Create: `.github/workflows/cockpit-collect.yml`, `docs/cockpit/setup.md`
- Test: `tests/workflows/test_cockpit_collect.py`

**Interfaces:**
- Triggers: `workflow_call`, `workflow_run` (`iac-staged`, `iac-nightly` completed), and cron `*/30 * * * *` as a fallback. It uses `actions/create-github-app-token` (pinned) with a read-only App. It writes `snapshot.json` plus `history/YYYY/MM/DD/HHMM.json` to the `cockpit-data` repo, then builds and deploys the site (Task 20) or runs the Markdown renderer (Task 21) depending on the input `renderer: pages|issue|summary`.

- [ ] **Step 1: Write failing tests:** `test_concurrency_single_collector` (group `ayasee-cockpit`, no cancel), `test_app_token_read_only_permissions`, `test_renderer_input_enum`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Write the workflow and `setup.md`. Include the App permissions list and the visibility guidance per host: private Pages on GHEC; on GHES, Pages are instance-wide, so use the `issue` renderer or accept sanitized data only.
- [ ] **Step 4:** Run the tests and `actionlint`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(cockpit): collection workflow and data repo layout`.

### Task 20: Cockpit site, estate matrix

**Files:**
- Create: `cockpit-site/{package.json,vite.config.ts,index.html,src/main.ts,src/model.ts,src/matrix.ts,src/status.ts,src/styles.css}`, `cockpit-site/test/{status.test.ts,matrix.test.ts}`, `cockpit-site/test/fixtures/snapshot.json`

**Interfaces:**
- `model.ts`: TypeScript types generated from `schema/snapshot.v1.json` (`json-schema-to-typescript`, a script in `package.json`).
- `status.ts`: `export function cellStatus(e: EnvState): "synced"|"pending"|"waiting"|"drift"|"failed"|"held"|"unknown"`. Precedence, first match wins: held (stop_the_line) > failed > drift > waiting > pending (pending_commits > 0) > synced > unknown.
- `matrix.ts`: `export function renderMatrix(s: Snapshot, root: HTMLElement): void`. Rows are repos, columns are the env order from the snapshot. Each cell shows the short SHA, a status pill, and the pending count. Cells are keyboard-focusable.

- [ ] **Step 1: Write failing tests** (vitest + jsdom):
  - `cellStatus` precedence table tests: one test per pair, including `held beats failed` and `drift beats waiting`.
  - `renderMatrix` renders `repos × envs` cells.
  - `unknown cell shows "—"`.
  - The status pills carry text, not colour only (accessibility).
- [ ] **Step 2:** Run `npm --prefix cockpit-site test`. Expected: FAIL.
- [ ] **Step 3:** Implement with no framework: vanilla TypeScript and DOM.
- [ ] **Step 4:** Run the tests and `npm --prefix cockpit-site run build`. Expected: PASS, and `dist/` is emitted.
- [ ] **Step 5:** Commit `feat(cockpit-site): estate matrix`.

### Task 21: Cockpit site detail drawer, history, and the Markdown renderer

**Files:**
- Create: `cockpit-site/src/drawer.ts`, `cockpit-site/src/history.ts`, `cockpit-site/test/{drawer.test.ts,history.test.ts}`, `src/ayasee/cockpit/render_md.py`, `tests/cockpit/test_render_md.py`

**Interfaces:**
- `drawer.ts`: `openDrawer(repo, env)` shows live SHA and time, the pending compare link, the gate run link, the drift issue link, findings counts with a code-scanning link, and the last result. Every action is a deep link to GitHub; nothing writes.
- `history.ts`: `loadHistory(baseUrl, days: number): Promise<Snapshot[]>` and `promotionLeadTime(history, repo): number | null` (median hours from dev to prod for the same SHA).
- `render_md.py`: `render(snapshot: dict) -> str`, a Markdown table with the same precedence as `cellStatus`. CLI `ayasee cockpit render-md --snapshot … --issue-number N` updates a pinned issue.

- [ ] **Step 1: Write failing tests:** `drawer has no buttons that POST` (assert there's no `form` or `fetch` call with `method: "POST"` in drawer output), `promotionLeadTime median`, and `test_md_same_status_precedence_as_site` (share the precedence fixture at `schema/fixtures/status-precedence.json` between pytest and vitest).
- [ ] **Step 2:** Run both test suites. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run both test suites. Expected: PASS.
- [ ] **Step 5:** Commit `feat(cockpit): detail drawer, history and Markdown renderer`.

---

## Phase 5 — Adoption and release

### Task 22: Reference adopter repo (Databricks + Azure)

**Files:**
- Create: `examples/databricks-azure/` with `stages/{dev,uat,prod}/{workspace,unity-catalog,grants}/terragrunt.hcl`, `root.hcl` (azurerm backend with `use_oidc`, `use_azuread_auth`), `.ayasee.yaml`, `.github/workflows/{pr.yml,main.yml,nightly.yml}` (callers pinned by SHA), `OWNERSHIP.md` (D13 contract), `.terraform.lock.hcl` per unit, `renovate.json` (Databricks provider with a "Breaking" release-notes check)

- [ ] **Step 1:** Write the example. Run `terragrunt run --all validate` and `conftest test` (pack) on a canned plan. Expected: valid, and the pack passes.
- [ ] **Step 2:** Run `uv run pytest tests/examples/test_example_wiring.py`. The test asserts every caller pins by SHA, no `secrets: inherit` is used, and every unit has a lock file. Expected: PASS.
- [ ] **Step 3:** Commit `docs(example): Databricks on Azure reference repo`.

### Task 23: Documentation set

**Files:**
- Create: `docs/concepts/{architecture.md,plan-binding.md,ledger.md,risk-gate.md,cockpit.md}`, `docs/guides/{quickstart.md,migrate-from-env-branches.md,onboard-brownfield.md}`, `docs/comparison.md`, `docs/compliance/` (from Task 15), `SECURITY.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`

- [ ] **Step 1:** Write `comparison.md` with a competitor matrix: tfaction, dflook, Atmos/Atmos Pro, Gruntwork, ALZ, Terramate, OpenTaco, Stategraph, Atlantis, SaaS, Kargo, Burrito. Include the honest wording on trunk practice ("Google's foundation blueprint is a current exception"), and a "use X instead if…" section for each tool.
- [ ] **Step 2:** Write `migrate-from-env-branches.md`, in phases: drain until `main = uat = prod`; cutover PR; no-op rehearsal; two-week bake; archive tags; delete the branches. Write `onboard-brownfield.md`: the exporter with `-services uc,idfed`, labelled experimental.
- [ ] **Step 3:** Run `npx markdown-link-check docs/**/*.md`. Expected: no broken links. Commit `docs: concepts, guides, comparison, compliance`.

### Task 24: GHES compatibility suite

**Files:**
- Create: `src/ayasee/features.py`, `tests/test_features.py`, `e2e/ghes/README.md`

**Interfaces:**
- `def detect(gh: GitHub) -> Features(queue_max: bool, deployment_false: bool, lockfiles: bool, private_pages: bool, oidc_issuer: str)`, from the `meta` endpoint plus probe calls. Workflows read `ayasee features --json` and choose paths accordingly.

- [ ] **Step 1: Write failing tests:** `test_ghes_issuer_format` (`https://HOST/_services/token`), `test_dotcom_defaults`, and `test_unknown_feature_defaults_false`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. Document how to run the e2e scenarios (Task 17) against a GHES instance, and record the verified version matrix.
- [ ] **Step 4:** Run the tests. Expected: PASS. Commit `feat(features): GHES feature detection and compatibility suite`.

### Task 25: Release v0.1.0

**Files:**
- Create: `.github/workflows/release.yml`, `CHANGELOG.md`

- [ ] **Step 1:** The release workflow builds the wheel and the cockpit `dist/`, generates artifact attestations (`actions/attest-build-provenance`, pinned), creates a GitHub release with the commit SHA in its notes for callers to pin, and publishes to PyPI via trusted publishing.
- [ ] **Step 2:** Do a dry run on a `v0.1.0-rc.1` tag. Expected: the release and attestations are created, and `gh attestation verify` passes on the wheel.
- [ ] **Step 3:** Point the example repo at the release SHA and re-run e2e. Expected: green. Tag `v0.1.0`. Commit `chore: release v0.1.0`.

---

## Task order and parallelism

- Phase 0 → Phase 1 (Tasks 2–9 are independent of each other after Task 2; Task 4 needs Task 3, Task 7 needs Task 6) → Phase 2 (independent, can run in parallel with Phase 1) → Phase 3 (Task 11 needs all of Phase 1; Tasks 12–15 need 11; 16 is independent; 17 needs 12–16) → Phase 4 (Task 18 needs 6; Tasks 19–21 need 18) → Phase 5.
- Critical path: 1 → 2 → 3 → 4 → 11 → 12 → 17 → 25.
- The cockpit (Tasks 18–21) can start right after Task 6, using fixture ledgers.
