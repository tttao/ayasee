"""All four stages succeed and the ledger shows dev, uat and prod at the merge SHA."""

from e2e.sandbox import Sandbox, expect
from e2e.scenarios.common import change, finish, run_to_gate

STAGES = ["dev", "uat", "prod-plan", "prod"]


def run(sandbox: Sandbox) -> None:
    sha = sandbox.ship(change(sandbox), "e2e: valid change")
    gate_run = run_to_gate(sandbox, sha)
    sandbox.review(gate_run["id"], "prod", approve=True)
    done = finish(sandbox, gate_run["id"])
    expect(done["conclusion"] == "success", f"run concluded {done['conclusion']}")
    for stage in STAGES:
        got = sandbox.job_conclusion(gate_run["id"], stage)
        expect(got == "success", f"{stage} job: {got}")
    for env in ("dev", "uat", "prod"):
        rec = sandbox.ledger(env)
        expect(rec is not None, f"no ledger record for {env}")
        expect(rec.sha == sha, f"{env} ledger at {rec.sha[:12]}, expected {sha[:12]}")
        expect(rec.converged, f"{env} ledger record is not converged")
    prod = sandbox.ledger("prod")
    expect(bool(prod.approver), "prod ledger record has no approver")
