"""A reviewer rejects the prod gate: prod is not applied and a prod_rejected card is posted."""

from e2e.sandbox import Sandbox, expect
from e2e.scenarios.common import change, deploy, finish, run_to_gate


def run(sandbox: Sandbox) -> None:
    deploy(sandbox, "e2e: baseline for gate_reject")
    before = sandbox.ledger("prod")
    sha = sandbox.ship(change(sandbox), "e2e: change to be rejected")
    run_ = run_to_gate(sandbox, sha)
    sandbox.review(run_["id"], "prod", approve=False)
    done = finish(sandbox, run_["id"])
    expect(done["conclusion"] != "success", "a rejected run concluded success")
    prod = sandbox.job_conclusion(run_["id"], "prod")
    expect(prod not in ("success", None), f"prod job: {prod}")
    after = sandbox.ledger("prod")
    expect(after is not None and after.sha != sha, "prod ledger moved to the rejected commit")
    expect(after == before, "prod ledger changed although prod was rejected")
    titles = [e.title for e in sandbox.events_for(sha)]
    expect(
        any(t.startswith("Prod rejected") for t in titles),
        f"no prod_rejected card for {sha[:12]}; cards: {titles}",
    )
