"""Pieces the scenarios share. Timeouts are generous: a staged run installs tools in every job."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from e2e.sandbox import STAGED_WORKFLOW, Sandbox, expect

RUN_TIMEOUT = 45 * 60  # a whole staged run
STEP_TIMEOUT = 20 * 60  # one stage or gate showing up
PROD_BASE_STATE = ".ayasee-e2e-state/prod/base.tfstate"


def change(sb: Sandbox) -> dict[str, str | None]:
    """A commit that changes every unit of every stage (and so gives prod a plan)."""
    return {"e2e-marker.txt": f"{sb.nonce()}\n"}


def unit_files(stage: str, name: str, main_tf: str) -> dict[str, str | None]:
    """A new unit under stages/<stage>/<name> using the shared root.hcl."""
    return {
        f"stages/{stage}/{name}/terragrunt.hcl": (
            'include "root" {\n  path = find_in_parent_folders("root.hcl")\n}\n'
        ),
        f"stages/{stage}/{name}/main.tf": main_tf,
    }


def remove_files(stage: str, name: str) -> dict[str, str | None]:
    return {f"stages/{stage}/{name}/terragrunt.hcl": None, f"stages/{stage}/{name}/main.tf": None}


def start_run(sb: Sandbox, sha: str) -> dict[str, Any]:
    return sb.wait_for_run(STAGED_WORKFLOW, sha, STEP_TIMEOUT, until="any")


def run_to_gate(sb: Sandbox, sha: str) -> dict[str, Any]:
    """The staged run of ``sha``, once its prod job waits for a reviewer."""
    run = start_run(sb, sha)
    sb.wait_for_pending(run["id"], "prod", STEP_TIMEOUT)
    return run


def finish(sb: Sandbox, run_id: int) -> dict[str, Any]:
    return sb.wait_for_completed(run_id, RUN_TIMEOUT)


def deploy(sb: Sandbox, message: str) -> tuple[str, int]:
    """Ship a change and approve prod; the staged run must succeed. Returns (sha, run id)."""
    sha = sb.ship(change(sb), message)
    run = run_to_gate(sb, sha)
    sb.review(run["id"], "prod", approve=True)
    done = finish(sb, run["id"])
    expect(
        done["conclusion"] == "success", f"baseline deploy of {sha[:12]} ended {done['conclusion']}"
    )
    return sha, run["id"]


def best_effort(what: str, action: Callable[[], object]) -> None:
    """Run a cleanup step from a ``finally`` block: report its failure, never raise it, so the
    scenario's own error (the one that matters) reaches the caller."""
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        print(f"e2e: cleanup step {what!r} failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def reject_leftovers(sb: Sandbox, run_ids: list[int]) -> None:
    """Reject every gate still waiting on these runs. Runs in ``finally`` blocks, so it must never
    raise: an error here would hide the failure that got us there."""
    for run_id in run_ids:
        try:
            for pending in sb.pending_deployments(run_id):
                sb.review(run_id, pending["environment"]["name"], approve=False)
        except Exception:  # noqa: BLE001 - best effort cleanup
            continue


def marker_resource(state: dict[str, Any]) -> dict[str, Any]:
    for res in state.get("resources", []):
        if res.get("type") == "terraform_data" and res.get("name") == "marker":
            return res["instances"][0]["attributes"]
    raise AssertionError("state has no terraform_data.marker")


def tamper_marker(sb: Sandbox, value: str) -> str:
    """Set prod/base's marker input in state (a manual state edit); returns the old value."""
    seen: dict[str, str] = {}

    def edit(state: dict[str, Any]) -> None:
        attrs = marker_resource(state)
        seen["old"] = str(attrs["input"])
        attrs["input"] = value

    sb.edit_state(PROD_BASE_STATE, edit)
    return seen["old"]
