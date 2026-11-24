## Why

A pull request that wires the ayasee callers before the first unit exists (the natural order for a
monorepo adopter: callers, bootstrap and `root.hcl` in one merge, then each unit in its own) fails
`plan-first-stage` with `no *.tfplan files found` and `plan-dev` reports a failed plan (issue #56).
The same commit would also fail every stage job of `iac-staged` on its first push to `main`. A stage
whose units directory exists but holds no unit yet has nothing to plan, and that is not a failure.
Unit discovery also ran in a process substitution, where its own refusals (`no units found`,
`duplicate unit name`, `unit discovery failed`) could not stop the script: the run only failed later,
by accident, in the binding step.

## What Changes

- The plan action gets an `allow-no-units` input (default `false`) and a `no-units` output. With it,
  a units directory that exists but holds no unit plans nothing and succeeds (`no-units=true`,
  `has-changes=false`, no manifest, change set, plan or plan JSON). A missing directory, a discovery
  failure, a duplicate unit name or a failed plan still fail; it is refused with the `drift` and
  `breakglass` binding modes.
- Unit discovery runs in the main shell of `plan.sh`, `apply.sh` and the cascade check, so its
  refusals stop the script at once. Without `allow-no-units`, an empty stage fails with
  `no units found under <dir>` instead of a binding error.
- `iac-pr`: `plan-first-stage` passes `allow-no-units`; the policy pack runs only when there is a
  plan; the comment says "No units under `<units_root>/<stage>`: nothing was planned", and warns when
  the base commit has units there.
- `iac-staged`: a pre-prod stage without units skips the apply and writes a ledger record at the
  SHA with no units, converged, so the guard chain holds. `prod-plan` without prod units skips the
  scans and the upload and outputs `no-units`; `prod-in-sync` then records prod the same way, also
  when the gate settings changed (the change still needs an approval at the first prod plan with
  units, because the trust anchor only moves on an approved record).
- Ledger: `ayasee ledger write --no-units` (ledger action input `no-units`) writes such a record.
  It refuses `--units-json`, `--approver`, `--breakglass-incident` and `--requester` with it. Empty
  `units` from `--units-json` are still refused.
- `iac-nightly`: an environment whose live record has no units is skipped with a notice.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plan-binding`: the plan action's explicit handling of a stage without units.
- `pr-checks`: the pull-request plan of a first stage without units.
- `staged-promotion`: pre-prod stages and prod without units; the empty-plan path.
- `ledger`: records of a stage without units.
- `stage-guard`: a predecessor recorded without units.
- `drift-detection`: skip an environment whose live record has no units.

## Impact

- `actions/plan` (`plan.sh`, `action.yml`), `actions/apply/apply.sh`, `actions/lib/ayasee-lib.sh`,
  `actions/ledger/action.yml`, `src/ayasee/ledger.py`.
- `.github/workflows/iac-pr.yml`, `iac-staged.yml`, `iac-nightly.yml`.
- Docs: `docs/concepts/pr-checks.md`, `staged-run.md`, `ledger.md`, `drift.md`, `plan-binding.md`,
  `examples/cloudflare-s3/README.md`; `CHANGELOG.md`.
- Callers need no change. A ledger reader that assumed at least one unit per record must accept an
  empty `units` object (the guard, the drift check and the cockpit do).
