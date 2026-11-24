## 1. Failing tests

- [x] 1.1 Integration tests of `plan.sh` and `apply.sh` with an empty units directory: fail closed by default, `allow-no-units` plans nothing, a missing directory and a discovery failure still fail, refusals with drift, breakglass and only-units
- [x] 1.2 Unit tests of `ayasee ledger write --no-units` and `record_from_run(no_units=True)`, and of the guard with a predecessor recorded without units
- [x] 1.3 Workflow tests: `iac-pr` (allow-no-units, policy only with a plan, the comment text), `iac-staged` (pre-prod slots, `prod-plan`, `prod-in-sync`, prod routing), `iac-nightly` (skip a record without units)

## 2. Implementation

- [x] 2.1 `ayasee-lib.sh`: `sg_units` prints nothing for an empty stage; `sg_read_units` runs discovery in the main shell; `sg_edges` checks the discovery status
- [x] 2.2 Plan action: `allow-no-units` input, `no-units` output, refusals; apply action reads units in the main shell
- [x] 2.3 Ledger: `--no-units` in the CLI and `no-units` in the ledger action
- [x] 2.4 `iac-pr.yml`: plan with `allow-no-units`, policy only with a plan, the no-units comment and the base-units warning
- [x] 2.5 `iac-staged.yml`: pre-prod slots, `prod-plan` outputs and skipped steps, routing of `prod`, `prod-approval` and `prod-in-sync`, the no-units prod record
- [x] 2.6 `iac-nightly.yml`: skip a live record without units

## 3. Docs

- [x] 3.1 `docs/concepts/pr-checks.md`, `staged-run.md`, `ledger.md`, `drift.md`, `plan-binding.md`, `examples/cloudflare-s3/README.md`
- [x] 3.2 `CHANGELOG.md` under `[Unreleased]`

## 4. Checks and archive

- [x] 4.1 ruff, pytest (unit and integration), shellcheck, check-links, actionlint, zizmor
- [x] 4.2 Validate and archive the change, then `openspec validate --specs --strict`
