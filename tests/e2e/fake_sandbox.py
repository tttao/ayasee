"""In-memory stand-in for e2e.sandbox.Sandbox that plays out the outcomes the scenarios expect.

It models just enough of the staged run: which files a commit touches decides the plan, a review
at the prod gate runs the guard, prod jobs queue behind each other (one concurrency group), state
edits bump a serial, nightly compares state with the applied marker. ``faults`` switch single
behaviours off so a scenario's assertions can be seen to fail (the fail direction). The ``wait_*``
methods honour their ``timeout`` argument semantics: a condition that never holds raises
SandboxTimeout."""

from __future__ import annotations

from itertools import count
from typing import Any

from e2e.sandbox import PullRequest, SandboxTimeout, WebhookEvent

from ayasee.config import NON_COMPLIANT
from ayasee.github import GitHubError
from ayasee.labels import APPLY_FAILED_LABEL, DRIFT_LABEL, POST_APPROVAL_LABEL, STOP_LABEL
from ayasee.ledger import UnitResult, record_from_run

GENERATION, TAG = "e2e-generation.txt", "e2e-grants-tag.txt"


def card(title: str, sha: str, env: str = "prod", *texts: str) -> WebhookEvent:
    return WebhookEvent(
        title=f"{title} · o/n-sandbox",
        status="",
        facts={"Commit": sha[:12], "Environment": env},
        texts=list(texts),
    )


class FakeSandbox:
    repo = "o/ayasee-sandbox"
    login = "pusher"

    def __init__(self, faults: set[str] | None = None) -> None:
        self.faults = faults or set()
        self._ids = count(1)
        self._sha = count(1)
        self.runs_by_id: dict[int, dict[str, Any]] = {}
        self.workflow_runs: list[dict[str, Any]] = []
        self.ledgers: dict[str, Any] = {}
        self.issue_list: list[dict[str, Any]] = []
        self.events: list[WebhookEvent] = []
        self.prs: dict[str, PullRequest] = {}
        self.state_serial = 0
        self.plan_serial: dict[int, int] = {}
        self.state_marker = "applied"
        self.baseline_marker = "applied"
        self.files = {GENERATION: "g0\n", TAG: "t0\n", ".ayasee.yaml": "stages: [dev, uat, prod]\n"}
        self.approved_by_self: set[int] = set()

    # ---- helpers -----------------------------------------------------------------------

    def nonce(self) -> str:
        return f"n{next(self._ids)}"

    def _new_sha(self) -> str:
        return f"{next(self._sha):040x}"

    def read_file(self, path, ref="main"):
        return self.files.get(path)

    def _record(
        self, env, sha, status="applied", incident=None, converged=True, units=None, approver=None
    ):
        if env == "prod" and status == "applied" and "no_ledger_prod" in self.faults:
            return
        if env == "dev" and "no_ledger_dev" in self.faults:
            return
        if approver is None:
            approver = "approver" if env == "prod" and "no_approver" not in self.faults else None
        self.ledgers[env] = record_from_run(
            repo=self.repo,
            env=env,
            sha=sha,
            run_id=1,
            run_attempt=1,
            units=units or {"base": UnitResult(status, converged)},  # type: ignore[arg-type]
            approver=approver,
            breakglass_incident=incident,
            now=f"2026-11-06T12:00:{next(self._ids) % 60:02d}.000000Z",
        )

    def _issue(self, title, labels, body="") -> None:
        self.issue_list.append(
            {
                "number": len(self.issue_list) + 1,
                "title": title,
                "state": "open",
                "labels": [{"name": n} for n in labels],
                "body": body,
            }
        )

    def _run(self, workflow, sha, jobs, pending=None, conclusion="success", queued=False):
        run = {
            "id": 1000 + next(self._ids),
            "head_sha": sha,
            "workflow": workflow,
            "status": "waiting" if pending else ("in_progress" if queued else "completed"),
            "conclusion": None if (pending or queued) else conclusion,
            "jobs": jobs,
            "pending": set(pending or ()),
            "queued_prod": queued,
            "log": {},
            "failed_step": {},
        }
        self.runs_by_id[run["id"]] = run
        self.workflow_runs.append(run)
        return run

    def _prod_busy(self) -> bool:
        return any(
            "prod" in r["pending"] for r in self.workflow_runs if r["workflow"] == "main.yml"
        )

    # ---- git and pull requests ---------------------------------------------------------

    def open_pr(self, files, message) -> PullRequest:
        sha = self._new_sha()
        pr = PullRequest(len(self.prs) + 1, f"e2e/{sha[:6]}", sha)
        self.prs[pr.branch] = pr
        if "scratch/insecure.tf" in files:
            bad = "scan_passes" not in self.faults
            self._run(
                "pr.yml",
                sha,
                {"checks": "failure" if bad else "success", "plan-dev": "success"},
                conclusion="failure" if bad else "success",
            )
        if any("badplan" in p for p in files):
            bad = "plan_passes" not in self.faults
            self._run(
                "pr.yml",
                sha,
                {"checks": "success", "plan-dev": "failure" if bad else "success"},
                conclusion="failure" if bad else "success",
            )
        if "staged_runs_for_pr" in self.faults:
            self._run("main.yml", sha, {}, conclusion="success")
        return pr

    def close_pr(self, pr) -> None:
        pass

    def ship(self, files, message) -> str:
        sha = self._new_sha()
        previous = dict(self.files)
        self.files.update({k: v for k, v in files.items() if v is not None})
        if files.get("stages/dev/boom/main.tf"):
            self._dev_apply_fail(sha)
        elif set(files) == {GENERATION, TAG}:
            reverting = (self.files[GENERATION], self.files[TAG]) == ("g0\n", "t0\n")
            if not reverting and all(self.files[k] != previous[k] for k in files):
                self._cascade(sha)
            else:
                self._empty(sha)
        elif "e2e-marker.txt" in files:
            self._change(sha)
            self._label(sha)
        else:
            self._empty(sha)
        return sha

    def _label(self, sha: str) -> None:
        """config labels: the resolve job prints NON-COMPLIANT for solo_mode (D2)."""
        if (
            "solo_mode: true" in self.files.get(".ayasee.yaml", "")
            and "no_solo_label" not in self.faults
        ):
            run = [r for r in self.workflow_runs if r["head_sha"] == sha][-1]
            run["log"]["resolve"] = f"::warning::{NON_COMPLIANT}"

    def _dev_apply_fail(self, sha: str) -> None:
        later = "uat_runs" in self.faults
        self._run(
            "main.yml",
            sha,
            {
                "dev": "failure",
                "uat": "success" if later else "skipped",
                "prod-plan": "skipped",
                "prod": "skipped",
            },
            conclusion="failure",
        )
        self._record("dev", sha, "failed", converged=False)
        if "no_stop_issue" not in self.faults:
            self._issue(
                "stop-the-line: dev apply failed", [STOP_LABEL, APPLY_FAILED_LABEL, "env:dev"]
            )

    def _change(self, sha: str) -> None:
        busy = self._prod_busy()
        run = self._run(
            "main.yml",
            sha,
            {"dev": "success", "uat": "success", "prod-plan": "success", "prod": "waiting"},
            pending=None if busy else {"prod"},
            queued=busy,
        )
        self.plan_serial[run["id"]] = self.state_serial
        self._record("dev", sha)
        self._record("uat", sha)

    def _cascade(self, sha: str) -> None:
        refused = "cascade_not_refused" not in self.faults
        jobs = {"dev": "success", "uat": "success"}
        if refused:
            jobs |= {"prod-plan": "failure", "prod": "skipped", "prod-in-sync": "skipped"}
            run = self._run("main.yml", sha, jobs, conclusion="failure")
            run["log"]["prod-plan"] = (
                "cascading change: grants depends on base, which also changes; land base first"
            )
        else:
            self._run(
                "main.yml",
                sha,
                {**jobs, "prod-plan": "success", "prod": "waiting"},
                pending={"prod"},
            )
        self._record("dev", sha)
        self._record("uat", sha)

    def _empty(self, sha: str) -> None:
        gate_runs = "gate_not_skipped" in self.faults
        self._run(
            "main.yml",
            sha,
            {
                "dev": "success",
                "uat": "success",
                "prod-plan": "success",
                "prod": "waiting" if gate_runs else "skipped",
                "prod-in-sync": "success",
            },
            pending={"prod"} if gate_runs else None,
        )
        for env in ("dev", "uat"):
            self._record(env, sha, "no_changes")
        if "ledger_not_moved" not in self.faults:
            self._record("prod", sha, "no_changes")

    def merge_pr(self, branch) -> str:
        return self.prs[branch].head_sha

    def edit_state(self, path, change) -> str:
        state = {
            "serial": self.state_serial,
            "resources": [
                {
                    "type": "terraform_data",
                    "name": "marker",
                    "instances": [{"attributes": {"input": self.state_marker}}],
                }
            ],
        }
        change(state)
        self.state_serial += 1
        self.state_marker = state["resources"][0]["instances"][0]["attributes"]["input"]
        return self._new_sha()

    # ---- runs, jobs, approvals ---------------------------------------------------------

    def wait_for_run(self, workflow, head_sha, timeout, *, until="completed", event=None):
        for r in reversed(self.workflow_runs):
            if r["workflow"] == workflow and r["head_sha"] == head_sha:
                if until == "any" or r["status"] == until:
                    return r
        raise SandboxTimeout(f"{workflow} at {head_sha[:12]} never {until}")

    def runs(self, workflow, head_sha):
        return [
            r for r in self.workflow_runs if r["workflow"] == workflow and r["head_sha"] == head_sha
        ]

    def wait_for_completed(self, run_id, timeout):
        run = self.runs_by_id[run_id]
        if run["status"] != "completed":
            raise SandboxTimeout(f"run {run_id} never completed")
        return run

    def wait_for_pending(self, run_id, env, timeout):
        if env not in self.runs_by_id[run_id]["pending"]:
            raise SandboxTimeout(f"{env} never pending")
        return {"environment": {"name": env}}

    def wait_for_job(self, run_id, name, timeout):
        job = self.job(run_id, name)
        if job is None or job["conclusion"] in ("waiting", None):
            raise SandboxTimeout(f"job {name} never finished")
        return {**job, "status": "completed"}

    def pending_deployments(self, run_id):
        return [{"environment": {"name": e}} for e in sorted(self.runs_by_id[run_id]["pending"])]

    def job(self, run_id, name):
        run = self.runs_by_id[run_id]
        if name not in run["jobs"]:
            return None
        step = run["failed_step"].get(name)
        return {
            "name": name,
            "id": hash((run_id, name)),
            "conclusion": run["jobs"][name],
            "steps": [{"name": step, "conclusion": "failure"}] if step else [],
        }

    def job_conclusion(self, run_id, name):
        j = self.job(run_id, name)
        return None if j is None else j["conclusion"]

    def job_log(self, run_id, name):
        return self.runs_by_id[run_id]["log"].get(name, "")

    def review(self, run_id, env, approve, *, as_self=False) -> None:
        run = self.runs_by_id[run_id]
        if env not in run["pending"]:
            raise AssertionError(f"run {run_id} is not waiting for {env}")
        if as_self:
            if "self_approval_refused" in self.faults:  # prevent_self_review left on
                raise GitHubError(422, "you cannot approve your own deployment")
            if "approver_not_self" not in self.faults:
                self.approved_by_self.add(run_id)
        run["pending"].discard(env)
        run["status"] = "completed"
        sha = run["head_sha"]
        if env == "prod-breakglass":
            return self._breakglass(run, approve)
        if not approve:
            run["jobs"]["prod"], run["conclusion"] = "failure", "failure"
            # R50: the prod-result job posts the card because the prod job failed without a step
            if "no_prod_result" not in self.faults and "no_rejected_card" not in self.faults:
                self.events.append(card("Prod rejected", sha))
            if "prod_applied_on_reject" in self.faults:
                self._record("prod", sha)
        else:
            self._apply(run, run_id, sha)
        self._release_queue()

    def _apply(self, run, run_id, sha) -> None:
        superseded = self.ledgers["uat"].sha != sha and "no_supersession" not in self.faults
        stale = self.state_serial != self.plan_serial[run_id] and "stale_applies" not in self.faults
        if superseded:
            run["jobs"]["prod"], run["conclusion"] = "failure", "failure"
            run["failed_step"]["prod"] = "Run ./actions/guard"
            run["log"]["prod"] = f"guard: rejected prod@{sha[:12]}: superseded"
        elif stale:
            run["jobs"]["prod"], run["conclusion"] = "failure", "failure"
            run["failed_step"]["prod"] = "Run ./actions/apply"
            run["log"]["prod"] = (
                "ayasee: base apply: failed (exit 1); engine output is in /tmp/x/logs\n"
                "ayasee: a unit failed, did not converge or was skipped; see units.json"
            )
            self._record(
                "prod",
                sha,
                units={"base": UnitResult("failed", False), "grants": UnitResult("skipped", False)},
                converged=False,
            )
            self._issue(
                "stop-the-line: prod apply failed", [STOP_LABEL, APPLY_FAILED_LABEL, "env:prod"]
            )
            if "no_failed_card" not in self.faults:
                self.events.append(card("Prod failed", sha, "prod", "Plan refused: re-plan"))
        else:
            run["jobs"]["prod"], run["conclusion"] = "success", "success"
            run["log"]["prod"] = "ayasee: base apply: ok\nayasee: grants apply: ok"
            self._record(
                "prod", sha, approver=self.login if run_id in self.approved_by_self else None
            )

    def _release_queue(self) -> None:
        if self._prod_busy():
            return
        for r in self.workflow_runs:
            if r.get("queued_prod"):
                r["queued_prod"] = False
                r["pending"] = {"prod"}
                r["status"] = "waiting"
                r["conclusion"] = None
                return

    # ---- nightly and break-glass -------------------------------------------------------

    def dispatch(self, workflow, inputs, timeout, ref="main"):
        if workflow == "breakglass.yml":
            run = self._run(
                workflow, inputs["sha"], {"breakglass": "waiting"}, pending={"prod-breakglass"}
            )
            run["incident"] = inputs["incident"]
            return run
        sha = self.ledgers["prod"].sha
        drifted = self.state_marker != self.baseline_marker
        open_ = [
            i
            for i in self.issue_list
            if i["state"] == "open" and DRIFT_LABEL in [x["name"] for x in i["labels"]]
        ]
        if drifted and not open_ and "no_drift_issue" not in self.faults:
            labels = [DRIFT_LABEL, "env:prod"]
            if "no_stop_label" not in self.faults:
                labels.append(STOP_LABEL)
            self._issue("drift: prod", labels)
            self.events.append(card("Drift found", sha))
        elif not drifted and open_ and "issue_not_closed" not in self.faults:
            open_[0]["state"] = "closed"
            if "no_cleared_card" not in self.faults:
                self.events.append(card("Drift cleared", sha))
        return self._run(workflow, sha, {"drift-prod": "success"})

    def _breakglass(self, run, approve: bool) -> None:
        sha = run["head_sha"]
        run["conclusion"] = "success" if approve else "failure"
        if not approve:
            return
        incident = None if "no_incident" in self.faults else run["incident"]
        # R71: break-glass plans the whole prod tree, so every unit is recorded
        units = {"base": UnitResult("applied", True)}
        if "breakglass_partial" not in self.faults:
            units["grants"] = UnitResult("no_changes", True)
        self._record("prod", sha, incident=incident, units=units)
        if "no_post_approval" not in self.faults:
            self._issue(
                f"post-approval: break-glass {run['incident']}", [POST_APPROVAL_LABEL, "env:prod"]
            )
        self.events.append(card("Break-glass used", sha))

    # ---- observations ------------------------------------------------------------------

    def ledger(self, env):
        return self.ledgers.get(env)

    def issues(self, labels, state="open"):
        return [
            i
            for i in self.issue_list
            if i["state"] == state and set(labels) <= {x["name"] for x in i["labels"]}
        ]

    def webhook_events(self):
        return list(self.events)

    def events_for(self, sha):
        return [e for e in self.events if e.commit == sha[:12]]

    def cleanup(self) -> None:
        for i in self.issue_list:
            i["state"] = "closed"
