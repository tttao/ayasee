"""Driver for the end-to-end scenarios: pushes commits to a sandbox repo, waits for the real
workflow runs, approves or rejects with a second identity and reads back ledger, issues and cards.

Everything goes through :class:`ayasee.github.GitHub`; there is no local clone. Per R8/R49 the
unit tests run this class against respx and the scenarios against a fake; only the nightly
``e2e`` workflow (or a person) points it at a real ``*-sandbox`` repository.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from ayasee.github import GitHub, GitHubError
from ayasee.labels import DRIFT_LABEL, POST_APPROVAL_LABEL, STOP_LABEL
from ayasee.ledger import LedgerRecord

MAIN = "main"
STATE_BRANCH = "ayasee-state"  # sandbox-only: Terraform state files, see e2e/README.md
LEDGER_BRANCH = "ayasee-ledger"
LEDGER_PREFIX = "ayasee-ledger"
PR_WORKFLOW = "pr.yml"
STAGED_WORKFLOW = "main.yml"
NIGHTLY_WORKFLOW = "nightly.yml"
BREAKGLASS_WORKFLOW = "breakglass.yml"


SANDBOX_SUFFIX = "-sandbox"


class NotASandbox(ValueError):
    """The repository name does not end in ``-sandbox``: the driver refuses to touch it."""


def check_repo(repo: str) -> str:
    """Safety guard: scenarios merge to main and edit state, so only ``owner/<name>-sandbox``."""
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name or not name.endswith(SANDBOX_SUFFIX):
        raise NotASandbox(
            f"E2E_REPO must be owner/<name>{SANDBOX_SUFFIX}; refusing to run against {repo!r}"
        )
    return repo


class ScenarioFailure(AssertionError):
    """A scenario assertion did not hold (a real assert would vanish under ``python -O``)."""


class SandboxTimeout(Exception):
    """A run, job or approval request did not appear in time."""


def expect(condition: object, message: str) -> None:
    if not condition:
        raise ScenarioFailure(message)


@dataclass(frozen=True)
class PullRequest:
    number: int
    branch: str
    head_sha: str


@dataclass(frozen=True)
class WebhookEvent:
    """One captured Teams card: ``title`` like "Prod rejected · owner/repo"."""

    title: str
    status: str
    facts: dict[str, str]
    texts: list[str] = field(default_factory=list)

    @property
    def commit(self) -> str:
        return self.facts.get("Commit", "")

    @property
    def env(self) -> str:
        return self.facts.get("Environment", "")


def parse_card(payload: Any) -> WebhookEvent | None:
    """The first Adaptive Card of a Teams message envelope (or a bare card), else None."""
    card = payload
    if isinstance(payload, dict) and payload.get("type") == "message":
        attachments = payload.get("attachments") or []
        card = attachments[0].get("content") if attachments else None
    if not isinstance(card, dict) or card.get("type") != "AdaptiveCard":
        return None
    texts: list[str] = []
    facts: dict[str, str] = {}
    for block in card.get("body", []):
        if block.get("type") == "TextBlock":
            texts.append(str(block.get("text", "")))
        elif block.get("type") == "FactSet":
            facts = {str(f["title"]): str(f["value"]) for f in block.get("facts", [])}
    return WebhookEvent(
        title=texts[0] if texts else "",
        status=texts[1] if len(texts) > 1 else "",
        facts=facts,
        texts=texts,
    )


class Sandbox:
    def __init__(
        self,
        gh: GitHub,
        approver_gh: GitHub,
        repo: str,
        webhook_url: str | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        poll_start: float = 2.0,
        poll_max: float = 30.0,
    ) -> None:
        self.gh = gh
        self.approver_gh = approver_gh
        self.repo = check_repo(repo)
        self.webhook_url = webhook_url
        self._sleep = sleep
        self._clock = clock
        self._poll_start = poll_start
        self._poll_max = poll_max

    # ---- polling ---------------------------------------------------------------------

    def _poll(self, what: str, timeout: float, probe: Callable[[], Any]) -> Any:
        """Call ``probe`` with exponential backoff until it returns something truthy."""
        deadline = self._clock() + timeout
        delay = self._poll_start
        while True:
            found = probe()
            if found:
                return found
            if self._clock() + delay > deadline:
                raise SandboxTimeout(f"timed out after {timeout:.0f}s waiting for {what}")
            self._sleep(delay)
            delay = min(delay * 1.5, self._poll_max)

    @staticmethod
    def nonce() -> str:
        return uuid.uuid4().hex[:8]

    # ---- git ---------------------------------------------------------------------------

    def push_commit(
        self,
        files: dict[str, str | None],
        message: str,
        branch: str = MAIN,
        base: str = MAIN,
    ) -> str:
        """Commit ``files`` (``None`` deletes) to ``branch`` through the git data API.

        A branch that does not exist yet is created from the tip of ``base``."""
        tip = self.gh.get_ref_sha(self.repo, branch)
        parent = tip or self.gh.get_ref_sha(self.repo, base)
        if parent is None:
            raise GitHubError(0, f"neither branch {branch!r} nor base {base!r} exists")
        tree = self.gh.create_tree(self.repo, self.gh.commit_tree_sha(self.repo, parent), files)
        sha = self.gh.create_commit(self.repo, message, tree, [parent])
        if tip is None:
            self.gh.create_branch(self.repo, branch, sha)
        else:
            self.gh.update_branch(self.repo, branch, sha)
        return sha

    def open_pr(self, files: dict[str, str | None], message: str) -> PullRequest:
        branch = f"e2e/{self.nonce()}"
        sha = self.push_commit(files, message, branch=branch)
        number = self.gh.create_pull_request(self.repo, branch, MAIN, message)
        return PullRequest(number, branch, sha)

    def merge_pr(self, branch: str) -> str:
        """Merge the open pull request of ``branch`` (squash); returns the commit on main."""
        number = self.gh.find_pull_request(self.repo, branch)
        if number is None:
            raise GitHubError(0, f"no open pull request for {branch!r}")
        return self.gh.merge_pull_request(self.repo, number)

    def close_pr(self, pr: PullRequest) -> None:
        self.gh.close_pull_request(self.repo, pr.number)

    def ship(self, files: dict[str, str | None], message: str) -> str:
        """Branch, pull request, merge. Returns the merge commit, which the staged run deploys."""
        pr = self.open_pr(files, message)
        return self.merge_pr(pr.branch)

    def edit_state(self, path: str, change: Callable[[dict[str, Any]], None]) -> str:
        """Edit a Terraform state file on the sandbox state branch (a manual out-of-band change)."""
        raw = self.gh.get_file(self.repo, path, STATE_BRANCH)
        if raw is None:
            raise GitHubError(404, f"no state file {path} on {STATE_BRANCH}")
        state = json.loads(raw)
        change(state)
        state["serial"] = int(state.get("serial", 0)) + 1
        text = json.dumps(state, indent=2) + "\n"
        return self.push_commit({path: text}, f"e2e: edit {path}", branch=STATE_BRANCH)

    # ---- runs and jobs -----------------------------------------------------------------

    def wait_for_run(
        self,
        workflow: str,
        head_sha: str,
        timeout: float,
        *,
        until: str = "completed",
        event: str | None = None,
    ) -> dict[str, Any]:
        """The newest run of ``workflow`` for ``head_sha`` once it is ``completed`` (default),
        ``waiting`` (blocked on an environment approval) or merely ``any`` (it exists)."""
        if until not in ("completed", "waiting", "any"):
            raise ValueError("until must be completed, waiting or any")

        def probe() -> dict[str, Any] | None:
            runs = self.gh.list_workflow_runs(self.repo, workflow, head_sha, event)
            runs = [r for r in runs if r.get("head_sha") == head_sha]
            if not runs:
                return None
            run = max(runs, key=lambda r: r.get("id", 0))
            return run if until == "any" or run.get("status") == until else None

        return self._poll(f"{workflow} at {head_sha[:12]} to be {until}", timeout, probe)

    def runs(self, workflow: str, head_sha: str) -> list[dict[str, Any]]:
        """Every run of ``workflow`` for ``head_sha`` (no waiting)."""
        found = self.gh.list_workflow_runs(self.repo, workflow, head_sha)
        return [r for r in found if r.get("head_sha") == head_sha]

    def dispatch(
        self, workflow: str, inputs: dict[str, str], timeout: float, ref: str = MAIN
    ) -> dict[str, Any]:
        """Start a ``workflow_dispatch`` run and return it (once it exists, any status)."""
        before = {
            r["id"]
            for r in self.gh.list_workflow_runs(self.repo, workflow, event="workflow_dispatch")
        }
        self.gh.dispatch_workflow(self.repo, workflow, ref, inputs)

        def probe() -> dict[str, Any] | None:
            runs = self.gh.list_workflow_runs(self.repo, workflow, event="workflow_dispatch")
            new = [r for r in runs if r["id"] not in before]
            return max(new, key=lambda r: r["id"]) if new else None

        return self._poll(f"the dispatched {workflow} run", timeout, probe)

    def wait_for_completed(self, run_id: int, timeout: float) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            run = self.gh.get_run(self.repo, run_id)
            return run if run.get("status") == "completed" else None

        return self._poll(f"run {run_id} to complete", timeout, probe)

    def wait_for_job(self, run_id: int, name: str, timeout: float) -> dict[str, Any]:
        """The job ``name`` once it has finished (any conclusion)."""

        def probe() -> dict[str, Any] | None:
            j = self.job(run_id, name)
            return j if j is not None and j.get("status") == "completed" else None

        return self._poll(f"job {name} of run {run_id} to finish", timeout, probe)

    def jobs(self, run_id: int) -> list[dict[str, Any]]:
        return self.gh.list_run_jobs(self.repo, run_id)

    def job(self, run_id: int, name: str) -> dict[str, Any] | None:
        """The job called ``name`` (a reusable workflow's jobs are named ``caller / name``)."""
        for j in self.jobs(run_id):
            full = str(j.get("name", ""))
            if full == name or full.endswith(f" / {name}"):
                return j
        return None

    def job_conclusion(self, run_id: int, name: str) -> str | None:
        j = self.job(run_id, name)
        return None if j is None else (j.get("conclusion") or j.get("status"))

    def job_log(self, run_id: int, name: str) -> str:
        j = self.job(run_id, name)
        if j is None:
            raise GitHubError(0, f"no job {name!r} in run {run_id}")
        return self.gh.job_log(self.repo, int(j["id"]))

    # ---- approvals ---------------------------------------------------------------------

    def pending_deployments(self, run_id: int) -> list[dict[str, Any]]:
        return self.gh.pending_deployments(self.repo, run_id)

    def wait_for_pending(self, run_id: int, env: str, timeout: float) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            for d in self.pending_deployments(run_id):
                if d.get("environment", {}).get("name") == env:
                    return d
            return None

        return self._poll(f"run {run_id} to wait for environment {env}", timeout, probe)

    def review(self, run_id: int, env: str, approve: bool) -> None:
        """Approve or reject the ``env`` gate of a run as the second identity."""
        for d in self.approver_gh.pending_deployments(self.repo, run_id):
            environment = d.get("environment", {})
            if environment.get("name") == env:
                self.approver_gh.review_pending_deployments(
                    self.repo,
                    run_id,
                    [int(environment["id"])],
                    approve,
                    "e2e approve" if approve else "e2e reject",
                )
                return
        raise GitHubError(0, f"run {run_id} is not waiting for environment {env!r}")

    # ---- observations ------------------------------------------------------------------

    def ledger(self, env: str) -> LedgerRecord | None:
        """Newest record for ``env`` from the git-branch ledger (current.json and history[0])."""
        base = f"{LEDGER_PREFIX}/{self.repo}/{env}"
        found: list[LedgerRecord] = []
        current = self.gh.get_file(self.repo, f"{base}/current.json", LEDGER_BRANCH)
        if current is not None:
            found.append(LedgerRecord.from_dict(json.loads(current)))
        names = sorted(self.gh.list_dir(self.repo, f"{base}/history", LEDGER_BRANCH), reverse=True)
        if names:
            newest = self.gh.get_file(self.repo, f"{base}/history/{names[0]}", LEDGER_BRANCH)
            if newest is not None:
                found.append(LedgerRecord.from_dict(json.loads(newest)))
        return max(found, key=lambda r: r.recorded_at) if found else None

    def issues(self, labels: list[str], state: str = "open") -> list[dict[str, Any]]:
        return self.gh.list_issues(self.repo, labels, state)

    def webhook_events(self) -> list[WebhookEvent]:
        """Cards captured by the webhook echo: ``GET <E2E_WEBHOOK_URL>`` returns a JSON array of
        the posted bodies (see e2e/README.md)."""
        if not self.webhook_url:
            raise GitHubError(0, "E2E_WEBHOOK_URL is not set")
        try:
            resp = httpx.get(self.webhook_url, timeout=30.0)
            resp.raise_for_status()
            bodies = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubError(0, f"webhook echo unreadable ({type(exc).__name__})") from None
        if not isinstance(bodies, list):
            raise GitHubError(0, "webhook echo must return a JSON array")
        return [e for e in (parse_card(b) for b in bodies) if e is not None]

    def events_for(self, sha: str) -> list[WebhookEvent]:
        return [e for e in self.webhook_events() if e.commit == sha[:12]]

    # ---- hygiene -----------------------------------------------------------------------

    def cleanup(self) -> None:
        """Close the issues a scenario leaves behind (they would stop the next scenario)."""
        for label in (STOP_LABEL, DRIFT_LABEL, POST_APPROVAL_LABEL):
            for issue in self.issues([label]):
                self.gh.update_issue(self.repo, int(issue["number"]), state="closed")
