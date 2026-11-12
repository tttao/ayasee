"""Solo mode (issue #1, D2): one maintainer merges and approves their own prod change.

``solo_mode: true`` is NON-COMPLIANT: every run must say so, and the prod gate, break-glass and its
post-approval issue must still work with a single identity. Needs a sandbox whose prod and
prod-breakglass environments have ``prevent_self_review`` off (``solo_mode = true`` in the
github-environments module, or an ephemeral sandbox made without ``--prevent-self-review``).
"""

from ayasee.config import NON_COMPLIANT
from ayasee.github import GitHubError
from ayasee.labels import POST_APPROVAL_LABEL
from e2e.sandbox import BREAKGLASS_WORKFLOW, Sandbox, expect
from e2e.scenarios.common import (
    RUN_TIMEOUT,
    STEP_TIMEOUT,
    best_effort,
    change,
    finish,
    reject_leftovers,
    run_to_gate,
)

CONFIG = ".ayasee.yaml"
INCIDENT = "INC456"


def _approve_own(sandbox: Sandbox, run_id: int, env: str) -> None:
    try:
        sandbox.review(run_id, env, approve=True, as_self=True)
    except GitHubError as exc:
        expect(
            False,
            f"the maintainer cannot approve {env} ({exc}); solo mode needs prevent_self_review "
            "off there (solo_mode = true in the github-environments module)",
        )


def run(sandbox: Sandbox) -> None:
    original = sandbox.read_file(CONFIG)
    expect(original is not None, f"the sandbox has no {CONFIG}")
    expect("solo_mode" not in original, f"{CONFIG} already sets solo_mode")
    me = sandbox.login
    runs: list[int] = []
    try:
        sha = sandbox.ship(
            {CONFIG: original + "solo_mode: true\n", **change(sandbox)}, "e2e: solo mode on"
        )
        staged = run_to_gate(sandbox, sha)
        runs.append(staged["id"])
        log = sandbox.job_log(staged["id"], "resolve")
        expect(NON_COMPLIANT in log, "the run does not label solo_mode NON-COMPLIANT")
        _approve_own(sandbox, staged["id"], "prod")
        done = finish(sandbox, staged["id"])
        expect(done["conclusion"] == "success", f"self-approved run ended {done['conclusion']}")
        prod = sandbox.ledger("prod")
        expect(prod is not None and prod.sha == sha, "prod ledger is not at the solo commit")
        expect(prod.approver == me, f"prod approver {prod.approver!r}, not the maintainer {me!r}")

        sha = sandbox.ship(change(sandbox), "e2e: solo break-glass")
        normal = run_to_gate(sandbox, sha)
        runs.append(normal["id"])
        glass = sandbox.dispatch(
            BREAKGLASS_WORKFLOW, {"incident": INCIDENT, "sha": sha}, STEP_TIMEOUT
        )
        sandbox.wait_for_pending(glass["id"], "prod-breakglass", STEP_TIMEOUT)
        _approve_own(sandbox, glass["id"], "prod-breakglass")
        done = sandbox.wait_for_completed(glass["id"], RUN_TIMEOUT)
        expect(done["conclusion"] == "success", f"solo break-glass ended {done['conclusion']}")
        issues = sandbox.issues([POST_APPROVAL_LABEL, "env:prod"])
        expect(
            any(INCIDENT in str(i.get("title", "")) for i in issues),
            "no post-approval issue after a solo break-glass",
        )
    finally:
        reject_leftovers(sandbox, runs)
        best_effort(
            "restore the configuration",
            lambda: sandbox.ship({CONFIG: original}, "e2e: solo mode off"),
        )
        best_effort("close issues", sandbox.cleanup)
