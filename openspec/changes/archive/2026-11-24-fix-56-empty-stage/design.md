## Context

`iac-staged` chains its stages through the ledger: the guard of each stage requires the preceding
recordable stage to have this SHA live and converged (D4), and the `prod` job runs it again after
the approval wait, so a newer commit that reached the stage before prod supersedes a waiting run.
A ledger record with empty `units` was refused, so that an apply that produced no outcome could
never read as "converged". `prod-in-sync` refused an empty `units` list for the same reason.

A stage can legitimately have no units: before the first unit lands (issue #56), and in a layout
where every unit of a pre-prod stage is a shared unit under `prod` (`shared_units`), which used to
fail the stage job too. `ayasee init` scaffolds each stage as an empty directory with a `.gitkeep`.

## Goals / Non-Goals

**Goals:**
- A pull request and a push that land the wiring before the first unit pass, and say that nothing
  was planned and why.
- A later run with units keeps a consistent guard chain, supersession included.
- A real failure (a plan that fails, a discovery failure, a missing directory) still fails.

**Non-Goals:**
- Destroying the resources of removed units (ayasee never does).
- Changing who may approve prod, or the trust anchor of the workflow gate.

## Decisions

**1. Only an existing, empty units directory counts as "no units".** A missing
`<units_root>/<stage>` still fails (`units directory not found`). A typo in `units_root` therefore
fails instead of reading as an empty estate, and `init` already creates the directory.
*Rejected:* treating a missing directory as empty (a mistyped `units_root` would turn every stage
green and stop drift checks).

**2. Opt-in at the plan action (`allow-no-units`), off by default.** The callers that may meet an
empty stage on purpose (`plan-first-stage`, the pre-prod slots, `prod-plan`) pass it; drift and
break-glass are refused with it (they plan what is live), and the apply action never accepts an
empty stage. The plan action outputs only `no-units=true` and `has-changes=false`, so a consumer
that forgets the case finds no manifest or change set and fails instead of passing.

**3. An empty stage writes a ledger record with no units, converged.** *Rejected:* writing no
record. The guard of the next stage would then see an older record (`superseded`) or none
(`predecessor_missing`), and the supersession check at the prod apply would lose the stage before
prod. Teaching the guard to read the working tree instead would add a second source of truth.
The record is truthful: at this SHA the stage has nothing to deploy. The refusal of empty `units`
stays the default; only the explicit `--no-units` flag writes such a record, and it refuses an
approver, an incident and a requester, so a record without units can never become the workflow
gate's trust anchor (`last_approved` reads approved records only).

**4. Prod without units goes to `prod-in-sync`, even when `gate-changed` is true.** There is no
plan to approve, and the prod jobs need a manifest. The in-sync record carries no approver, so the
anchor of the workflow gate does not move and the first prod plan with units still reports
`gate-changed` and needs an approval. On the first push with `gate_mechanism: workflow`,
`gate-changed` is true (no approved prod apply yet), so failing here would block exactly the case
the issue describes.

**5. The nightly drift check skips an environment whose live record has no units**, with a notice,
before it logs in or plans; the plan action stays strict in drift mode.

**6. Unit discovery runs in the main shell.** It used to run in `< <(...)`, where `sg_die` ends
only the subshell. With `allow-no-units`, an empty list from a failed discovery would otherwise
pass (fail open).

## Risks / Trade-offs

- [A pull request that deletes every first-stage unit, or points `units_root` at another empty
  directory, gets a green plan] → the comment names the directory it looked at and warns when the
  base commit has units in that stage; the diff is reviewed; nothing is destroyed on merge, and a
  later pull request that brings the units back plans all of them.
- [A commit that removes every unit of an environment stops its drift checks] → the same class as
  removing one unit today (its resources are no longer planned); the docs say so.
- [Readers of the ledger that assumed at least one unit] → the guard, drift check, cockpit and
  approval code read `units` as a mapping and accept an empty one (tested).
