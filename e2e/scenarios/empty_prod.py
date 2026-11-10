"""A change with nothing for prod: the gate is skipped and the prod ledger SHA still moves."""

from e2e.sandbox import Sandbox, expect
from e2e.scenarios.common import deploy, finish, start_run


def run(sandbox: Sandbox) -> None:
    deploy(sandbox, "e2e: baseline for empty_prod")
    sha = sandbox.ship({"docs/note.md": f"note {sandbox.nonce()}\n"}, "e2e: docs only")
    run_ = start_run(sandbox, sha)
    done = finish(sandbox, run_["id"])
    expect(done["conclusion"] == "success", f"run concluded {done['conclusion']}")
    expect(
        sandbox.job_conclusion(run_["id"], "prod") in (None, "skipped"),
        "the prod gate job was not skipped",
    )
    sync = sandbox.job_conclusion(run_["id"], "prod-in-sync")
    expect(sync == "success", f"prod-in-sync job: {sync}")
    expect(not sandbox.pending_deployments(run_["id"]), "the run waited for a reviewer")
    prod = sandbox.ledger("prod")
    expect(prod is not None and prod.sha == sha, "prod ledger SHA did not move to the commit")
    expect(prod.converged, "prod ledger record is not converged")
    expect(
        all(u.status == "no_changes" for u in prod.units.values()),
        f"prod units were not all no_changes: {prod.units}",
    )
