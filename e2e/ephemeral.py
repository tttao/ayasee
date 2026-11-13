"""Spawn and destroy a throwaway sandbox repository for trying out and demonstrating ayasee.

    python -m e2e.ephemeral up [--name NAME] [--reviewer LOGIN ...] [--use-gh-token]
    python -m e2e.ephemeral pr  --repo OWNER/NAME [--text TEXT]
    python -m e2e.ephemeral status --repo OWNER/NAME
    python -m e2e.ephemeral down --repo OWNER/NAME

``up`` creates ``<owner>/<name>-sandbox`` with the e2e fixture, the four ayasee workflows and the
actions, CLI and policy they run, then the environments, secrets and a first staged run. Unlike
``e2e/seed.sh`` it needs no released ayasee commit and no cloud:

- **Self-pinned.** The first commit holds the ayasee code and no workflows; the second adds the
  workflows with their action references pinned to the first commit of the sandbox itself (the
  release form of ``scripts/set-ayasee-ref.sh``). Every workflow, nightly and break-glass too,
  runs the code of this checkout.
- **Cloudless.** The sandbox copies drop the ``azure/login`` steps (the fixture creates no cloud
  resources) and encrypt the prod plan with the test-only ``AYASEE_TEST_PASSPHRASE`` instead of
  a Key Vault key. The passphrase is generated, stored as the secret ``PLAN_PASSPHRASE`` and reaches
  the plan and apply steps through a sandbox-only ``plan-passphrase`` workflow secret.

Everything goes through the ``gh`` CLI, so its login is the identity (scopes ``repo`` and
``workflow``; ``down`` also needs ``delete_repo``). The ledger and the fixture's state sync push
with a contents-write token: ``SANDBOX_CONTENTS_TOKEN`` (a fine-grained token on the sandbox), or
the ``gh`` token itself with ``--use-gh-token`` (broader than needed; the sandbox is yours, but
know it).

Never point this at a real repository: like the e2e runner it only touches ``*-sandbox`` names, and
``down`` refuses a repository that ``up`` did not mark with the ``ayasee-ephemeral`` topic.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from e2e.sandbox import LEDGER_BRANCH, SANDBOX_SUFFIX, STATE_BRANCH, NotASandbox, check_repo

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "e2e" / "fixture"
TOPIC = "ayasee-ephemeral"
DESCRIPTION = "Ephemeral ayasee sandbox (python -m e2e.ephemeral); safe to delete"
WORKFLOWS = ("iac-pr", "iac-staged", "iac-nightly", "iac-breakglass")
CALLERS = {"nightly": "iac-nightly", "breakglass": "iac-breakglass"}
STAGES = ("dev", "uat", "prod")
# Units that read another unit's outputs. On an empty repository prod-plan refuses them together
# with their upstream (R52, cascading change), so `up` lands them in a second staged run, the
# incremental bootstrap of examples/databricks-azure/README.md.
DEPENDENT_UNITS = ("grants",)
# The pull-request plan environment, <first stage>-plan (issue #11), as in iac-pr.
PR_PLAN = f"{STAGES[0]}-plan"
# Environment -> has reviewers (the gate). PR_PLAN is the only one not restricted to main,
# as in modules/github-environments.
ENVIRONMENTS = {
    **{s: False for s in STAGES},
    **{f"drift-{s}": False for s in STAGES},
    PR_PLAN: False,
    "prod-plan": False,
    "prod": True,
    "prod-breakglass": True,
}
GIT_ID = {
    "GIT_AUTHOR_NAME": "ayasee-ephemeral",
    "GIT_AUTHOR_EMAIL": "ephemeral@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "ayasee-ephemeral",
    "GIT_COMMITTER_EMAIL": "ephemeral@users.noreply.github.com",
}

Runner = Callable[..., subprocess.CompletedProcess[str]]


class EphemeralError(Exception):
    """Bad input or a failed step: the message says what to do."""


# --- building the content ---------------------------------------------------------------------

_ACTION_REF = re.compile(r"[^ ]*/ayasee/actions/([a-z-]*)@[0-9a-f]{40} # AYASEE_REF")
_WORKFLOW_REF = re.compile(
    r"OWNER/ayasee/\.github/workflows/([a-z-]*\.yml)@[0-9a-f]{40} # AYASEE_WORKFLOW_REF"
)
_EXTRA_ENV = re.compile(r"^( +)AYASEE_EXTRA_SECRET: \$\{\{ secrets\.extra-env-secret \}\}$", re.M)
_EXTRA_DECL = re.compile(r"^      extra-env-secret:\n", re.M)
_CALLER_SECRET = re.compile(r"^( +)extra-env-secret: \$\{\{ secrets\.STATE_TOKEN \}\}.*$", re.M)
_PASSPHRASE_DECL = (
    "      plan-passphrase:\n"
    "        description: >-\n"
    "          SANDBOX ONLY (e2e/ephemeral.py): exported as AYASEE_TEST_PASSPHRASE to the plan\n"
    "          and apply steps, replacing the Key Vault key of the plan encryption.\n"
    "        required: false\n"
)


def _drop_azure_login(text: str) -> tuple[str, int]:
    """Remove every ``- uses: azure/login@...`` step: the line and the lines indented under it."""
    out: list[str] = []
    lines = text.splitlines(keepends=True)
    dropped = i = 0
    while i < len(lines):
        m = re.match(r"^( *)- uses: azure/login@", lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        dropped += 1
        indent = len(m.group(1))
        i += 1
        while i < len(lines) and (
            not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip(" ")) > indent + 1
        ):
            i += 1
    return "".join(out), dropped


def pin_workflow(text: str, repo: str, sha: str) -> str:
    """The release form of scripts/set-ayasee-ref.sh: own actions at ``repo@sha``."""
    pinned, n = _ACTION_REF.subn(rf"{repo}/actions/\1@{sha} # AYASEE_REF", text)
    if n == 0:
        raise EphemeralError("no AYASEE_REF action reference found; has the workflow changed?")
    return pinned


def cloudless_workflow(text: str) -> str:
    """Sandbox-only: no Azure login, plan encryption from the ``plan-passphrase`` secret."""
    text, logins = _drop_azure_login(text)
    if logins == 0:
        raise EphemeralError("no azure/login step found; has the workflow changed?")
    if "azure/login" in text:
        raise EphemeralError("an azure/login reference survived the rewrite")
    if _EXTRA_DECL.search(text):
        text = _EXTRA_DECL.sub(lambda m: _PASSPHRASE_DECL + m.group(0), text, count=1)
        text, n = _EXTRA_ENV.subn(
            r"\g<0>\n\1AYASEE_TEST_PASSPHRASE: ${{ secrets.plan-passphrase }}", text
        )
        if n == 0:
            raise EphemeralError("plan-passphrase is declared but no plan or apply step gets it")
    return text


def ledger_config(text: str, name: str) -> str:
    """The git-branch ledger lives in the runner checkout, /home/runner/work/<name>/<name>."""
    new, n = re.subn(
        r"/home/runner/work/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\?",
        f"/home/runner/work/{name}/{name}?",
        text,
    )
    if n != 1:
        raise EphemeralError("the fixture's ledger URL has an unexpected shape")
    return new


def ledger_url(name: str) -> str:
    """The ledger URL of the sandbox ``name``, as its rewritten ``.ayasee.yaml`` holds it."""
    text = ledger_config((FIXTURE / ".ayasee.yaml").read_text(), name)
    m = re.search(r"^ledger:\n +url: (\S+)$", text, re.M)
    if not m:
        raise EphemeralError("the fixture's .ayasee.yaml has no ledger.url")
    return m[1]


def caller_workflow(text: str, stem: str, workflow: str) -> str:
    """Point a fixture caller at the in-repo reusable workflow and pass the plan passphrase."""
    if stem in CALLERS:
        text, n = _WORKFLOW_REF.subn(f"./.github/workflows/{workflow}.yml", text)
        if n != 1:
            raise EphemeralError(f"{stem}.yml: no AYASEE_WORKFLOW_REF line")
    return _CALLER_SECRET.sub(
        r"\g<0>\n\1plan-passphrase: ${{ secrets.PLAN_PASSPHRASE }} # sandbox-only: ephemeral.py",
        text,
    )


def build_source(dest: Path) -> None:
    """Commit 1: the fixture without its dependent units and the ayasee code (no workflows,
    so nothing runs yet)."""
    shutil.copytree(
        FIXTURE,
        dest,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".github", *DEPENDENT_UNITS),
    )
    for d in ("actions", "src", "rules", "policy"):
        shutil.copytree(ROOT / d, dest / d, ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("pyproject.toml", "uv.lock", "LICENSE", "NOTICE", ".python-version"):
        shutil.copy2(ROOT / f, dest / f)
    (dest / "README.md").write_text(
        "# ayasee ephemeral sandbox\n\n"
        "Created by `python -m e2e.ephemeral up` from the ayasee repository, for trying out\n"
        "and demonstrating ayasee. SANDBOX ONLY: Terraform state lives on the\n"
        "`ayasee-state` branch and the prod plan is encrypted with a passphrase instead of a\n"
        "Key Vault key.\n"
        "Delete it with `python -m e2e.ephemeral down --repo <owner>/<name>`.\n"
    )
    (dest / ".gitignore").write_text(".terraform/\n.ayasee-e2e-state/\n__pycache__/\n")


def build_workflows(dest: Path, repo: str, sha: str) -> None:
    """Commit 2: the reusable workflows pinned to commit 1, the callers and the ledger URL."""
    name = repo.split("/", 1)[1]
    wf = dest / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    for w in WORKFLOWS:
        text = (ROOT / ".github" / "workflows" / f"{w}.yml").read_text()
        (wf / f"{w}.yml").write_text(cloudless_workflow(pin_workflow(text, repo, sha)))
    for caller in sorted((FIXTURE / ".github" / "workflows").glob("*.yml")):
        stem = caller.stem
        (wf / caller.name).write_text(
            caller_workflow(caller.read_text(), stem, CALLERS.get(stem, ""))
        )
    cfg = dest / ".ayasee.yaml"
    cfg.write_text(ledger_config(cfg.read_text(), name))
    # Break-glass takes workflow_dispatch inputs by design; accept that finding the documented way
    # (docs/concepts/pr-checks.md#the-checkov-baseline) so the PR checks pass.
    baseline = {
        "failed_checks": [
            {
                "file": f"/.github/workflows/{f}.yml",
                "findings": [{"resource": f"on({f})", "check_ids": ["CKV_GHA_7"]}],
            }
            for f in ("breakglass", "iac-breakglass")
        ]
    }
    (dest / ".ayasee").mkdir(exist_ok=True)
    (dest / ".ayasee" / "checkov.baseline").write_text(json.dumps(baseline, indent=4) + "\n")


def add_dependent_units(dest: Path) -> None:
    """Commit 3: the units left out of commit 1, once their upstream units are live everywhere."""
    for stage in STAGES:
        for unit in DEPENDENT_UNITS:
            src = FIXTURE / "stages" / stage / unit
            shutil.copytree(src, dest / "stages" / stage / unit)


# --- talking to GitHub ------------------------------------------------------------------------


def _run(
    cmd: Sequence[str],
    *,
    input: str | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        input=input,
        cwd=cwd,
        env=None if env is None else {**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


class Ephemeral:
    def __init__(self, run: Runner = _run, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.run = run
        self.sleep = sleep

    def _ok(self, cmd: Sequence[str], **kw: object) -> str:
        p = self.run(cmd, **kw)
        if p.returncode != 0:
            shown = " ".join(cmd)[:200]
            raise EphemeralError(f"{shown} failed: {(p.stderr or p.stdout).strip()}")
        return p.stdout

    def api(self, path: str, *, method: str = "GET", body: object = None) -> object:
        cmd = ["gh", "api", "-X", method, path]
        if body is not None:
            cmd += ["--input", "-"]
        out = self._ok(cmd, input=None if body is None else json.dumps(body))
        return json.loads(out) if out.strip() else None

    def login(self) -> str:
        return str(self.api("/user")["login"])  # type: ignore[index]

    def user_id(self, login: str) -> int:
        return int(self.api(f"/users/{login}")["id"])  # type: ignore[index]

    def set_secret(self, repo: str, name: str, value: str) -> None:
        # the value goes through stdin, never on a command line
        self._ok(["gh", "secret", "set", name, "--repo", repo], input=value)

    def gh_token(self) -> str:
        return self._ok(["gh", "auth", "token"]).strip()

    # -- the steps of `up` --

    def create_repo(self, repo: str, private: bool) -> None:
        self._ok(
            [
                "gh",
                "repo",
                "create",
                repo,
                "--private" if private else "--public",
                "--description",
                DESCRIPTION,
                "--disable-wiki",
            ]
        )
        self.api(f"/repos/{repo}/topics", method="PUT", body={"names": [TOPIC]})

    def push(self, workdir: Path, repo: str, message: str) -> str:
        git = ["git", "-C", str(workdir)]
        self._ok([*git, "add", "-A"])
        self._ok([*git, "commit", "--quiet", "--no-gpg-sign", "-m", message], env=GIT_ID)
        sha = self._ok([*git, "rev-parse", "HEAD"]).strip()
        # gh is the credential helper for this push only
        self._ok(
            [
                "git",
                "-C",
                str(workdir),
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "push",
                "--quiet",
                f"https://github.com/{repo}.git",
                "HEAD:refs/heads/main",
            ]
        )
        return sha

    def environments(self, repo: str, reviewer_ids: list[int], prevent_self_review: bool) -> None:
        for env, gated in ENVIRONMENTS.items():
            body: dict[str, object] = {
                "prevent_self_review": prevent_self_review,
                "reviewers": [{"type": "User", "id": i} for i in reviewer_ids] if gated else [],
                "deployment_branch_policy": None
                if env == PR_PLAN
                else {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                },
            }
            self.api(f"/repos/{repo}/environments/{env}", method="PUT", body=body)
            if env != PR_PLAN:
                self.api(
                    f"/repos/{repo}/environments/{env}/deployment-branch-policies",
                    method="POST",
                    body={"name": "main", "type": "branch"},
                )
        # iac-breakglass reads the ledger URL from its environment only, never from the
        # configuration of the sha it deploys.
        self.api(
            f"/repos/{repo}/environments/prod-breakglass/variables",
            method="POST",
            body={"name": "AYASEE_LEDGER_URL", "value": ledger_url(repo.split("/", 1)[1])},
        )

    def up(
        self,
        *,
        name: str | None,
        reviewers: Sequence[str],
        private: bool,
        prevent_self_review: bool,
        contents_token: str | None,
        use_gh_token: bool,
        teams_webhook: str | None,
        approve: bool = False,
        reruns: int = 2,
        timeout: float = 3600,
        keep_workdir: Path | None = None,
    ) -> str:
        owner = self.login()
        repo = check_repo(f"{owner}/{name or default_name()}")
        if contents_token is None:
            if not use_gh_token:
                raise EphemeralError(
                    "the ledger and state sync need a contents-write token: set "
                    "SANDBOX_CONTENTS_TOKEN or pass --use-gh-token"
                )
            contents_token = self.gh_token()
        if teams_webhook is not None and not teams_webhook.startswith("https://"):
            raise EphemeralError("SANDBOX_TEAMS_WEBHOOK must be an https URL")
        reviewer_ids = [self.user_id(r) for r in (reviewers or [owner])]

        work = keep_workdir or Path(tempfile.mkdtemp(prefix="ayasee-ephemeral-"))
        try:
            self._ok(["git", "init", "--quiet", "-b", "main", str(work)])
            build_source(work)
            self.create_repo(repo, private)
            print(f"created https://github.com/{repo}")
            source = self.push(work, repo, "ayasee source (no workflows)")
            self.environments(repo, reviewer_ids, prevent_self_review)
            for k, v in {
                "LEDGER_TOKEN": contents_token,
                "STATE_TOKEN": contents_token,
                "PLAN_PASSPHRASE": secrets.token_urlsafe(32),
                **({"TEAMS_WEBHOOK": teams_webhook} if teams_webhook else {}),
            }.items():
                self.set_secret(repo, k, v)
            build_workflows(work, repo, source)
            who = ", ".join(reviewers or [owner])
            sha = self.push(work, repo, f"ayasee workflows pinned to {source[:12]}")
            self.await_staged(repo, sha, "base units", approve, who, reruns, timeout)
            add_dependent_units(work)
            sha = self.push(work, repo, "add the dependent units (grants read base's outputs)")
            self.await_staged(repo, sha, "dependent units", approve, who, reruns, timeout)
        finally:
            if keep_workdir is None:
                shutil.rmtree(work, ignore_errors=True)
        print(f"ready: dev, uat and prod are in sync at {sha[:12]}")
        print(f"next: python -m e2e.ephemeral pr --repo {repo}")
        return repo

    def await_staged(
        self,
        repo: str,
        sha: str,
        what: str,
        approve: bool,
        who: str,
        reruns: int,
        timeout: float,
        interval: float = 15,
    ) -> None:
        """Follow the staged run of ``sha`` to success: approve its prod gate (``approve``) or wait
        for a reviewer, and re-run failed jobs up to ``reruns`` times (GitHub release downloads
        fail for minutes at a time)."""
        run: dict | None = None
        announced = False
        for _ in range(max(1, int(timeout / interval))):
            if run is None:
                # not .../workflows/main.yml/runs: that is a 404 until GitHub registers the workflow
                runs = self.api(f"/repos/{repo}/actions/runs?head_sha={sha}&event=push")
                staged = [
                    r
                    for r in runs["workflow_runs"]  # type: ignore[index]
                    if r["path"] == ".github/workflows/main.yml"
                ]
                if staged:
                    run = staged[0]
                    print(f"staged run for the {what}: {run['html_url']}")
            else:
                run = self.api(f"/repos/{repo}/actions/runs/{run['id']}")  # type: ignore[assignment]
            if run is None:
                self.sleep(interval)  # the push event has not created the run yet
                continue
            if run["status"] == "waiting":
                pending = self.api(f"/repos/{repo}/actions/runs/{run['id']}/pending_deployments")
                ids = [d["environment"]["id"] for d in pending]  # type: ignore[union-attr]
                if ids and approve:
                    self.api(
                        f"/repos/{repo}/actions/runs/{run['id']}/pending_deployments",
                        method="POST",
                        body={
                            "environment_ids": ids,
                            "state": "approved",
                            "comment": "approved by e2e.ephemeral up --approve",
                        },
                    )
                    print("approved the prod gate (--approve)")
                elif ids and not announced:
                    print(f"prod waits for approval by {who}: {run['html_url']}")
                    announced = True
            elif run["status"] == "completed":
                if run["conclusion"] == "success":
                    return
                jobs = self.api(f"/repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest")
                failed = [
                    j["name"]
                    for j in jobs["jobs"]  # type: ignore[index]
                    if j["conclusion"] not in ("success", "skipped")
                ]
                if reruns <= 0:
                    raise EphemeralError(
                        f"the staged run for the {what} failed ({', '.join(failed)}): "
                        f"{run['html_url']}"
                    )
                reruns -= 1
                print(f"re-running the failed jobs ({', '.join(failed)})")
                self.api(f"/repos/{repo}/actions/runs/{run['id']}/rerun-failed-jobs", method="POST")
                announced = False
                self.sleep(interval)  # the run takes a moment to leave `completed`
            self.sleep(interval)
        raise EphemeralError(f"the staged run for the {what} did not finish in time")

    def pr(self, repo: str, text: str | None) -> str:
        check_repo(repo)
        branch = f"demo-{int(time.time())}"
        main = self.api(f"/repos/{repo}/git/ref/heads/main")["object"]["sha"]  # type: ignore[index]
        self.api(
            f"/repos/{repo}/git/refs",
            method="POST",
            body={"ref": f"refs/heads/{branch}", "sha": main},
        )
        current = self.api(f"/repos/{repo}/contents/e2e-marker.txt?ref={branch}")
        marker = text or f"demo-{branch.removeprefix('demo-')}"
        self.api(
            f"/repos/{repo}/contents/e2e-marker.txt",
            method="PUT",
            body={
                "message": f"demo: marker {marker}",
                "content": base64.b64encode(f"{marker}\n".encode()).decode(),
                "sha": current["sha"],  # type: ignore[index]
                "branch": branch,
            },
        )
        pr = self.api(
            f"/repos/{repo}/pulls",
            method="POST",
            body={
                "title": f"demo: change the base marker to {marker}",
                "head": branch,
                "base": "main",
                "body": "Changes `base` in every stage. Merge it to start dev, uat, prod-plan "
                "and the prod gate.",
            },
        )
        url = str(pr["html_url"])  # type: ignore[index]
        print(url)
        return url

    def status(self, repo: str) -> None:
        check_repo(repo)
        runs = self.api(f"/repos/{repo}/actions/runs?per_page=10")["workflow_runs"]  # type: ignore[index]
        print(f"https://github.com/{repo}")
        for r in runs:
            print(
                f"{r['created_at']}  {r['name']:<10} {r['status']:<11} "
                f"{r['conclusion'] or '-':<10} {r['head_sha'][:12]}  {r['html_url']}"
            )
        ledger = self.run(["gh", "api", f"/repos/{repo}/branches/{LEDGER_BRANCH}"])
        state = self.run(["gh", "api", f"/repos/{repo}/branches/{STATE_BRANCH}"])
        print(
            f"ledger branch: {'yes' if ledger.returncode == 0 else 'not yet'}; "
            f"state branch: {'yes' if state.returncode == 0 else 'not yet'}"
        )

    def down(self, repo: str) -> None:
        check_repo(repo)
        topics = self.api(f"/repos/{repo}/topics")["names"]  # type: ignore[index]
        if TOPIC not in topics:
            raise EphemeralError(f"{repo} has no {TOPIC} topic: not created by `up`, refusing")
        p = self.run(["gh", "repo", "delete", repo, "--yes"])
        if p.returncode != 0:
            hint = ""
            if "delete_repo" in (p.stderr + p.stdout) and "gh auth refresh" not in p.stderr:
                hint = "; grant the scope with: gh auth refresh -h github.com -s delete_repo"
            raise EphemeralError(f"deleting {repo} failed: {p.stderr.strip()}{hint}")
        print(f"deleted {repo}")


def default_name() -> str:
    return f"ayasee-{time.strftime('%Y%m%d-%H%M%S')}{SANDBOX_SUFFIX}"


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    eph: Ephemeral | None = None,
) -> int:
    env = os.environ if env is None else env
    summary = (__doc__ or "").split("\n")[0]
    parser = argparse.ArgumentParser(prog="python -m e2e.ephemeral", description=summary)
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up", help="create a sandbox repository and start its first staged run")
    up.add_argument(
        "--name",
        help=f"repository name, must end in {SANDBOX_SUFFIX} (default: ayasee-<timestamp>-sandbox)",
    )
    up.add_argument(
        "--reviewer",
        action="append",
        default=[],
        metavar="LOGIN",
        help="prod and break-glass reviewer (repeatable; default: you)",
    )
    up.add_argument(
        "--prevent-self-review",
        action="store_true",
        help="reviewers may not approve their own runs (needs a second reviewer)",
    )
    up.add_argument(
        "--private",
        action="store_true",
        help="private repository (environment reviewers then need a paid plan)",
    )
    up.add_argument(
        "--use-gh-token",
        action="store_true",
        help="store the gh token as LEDGER_TOKEN and STATE_TOKEN "
        "(when SANDBOX_CONTENTS_TOKEN is unset)",
    )
    up.add_argument(
        "--approve",
        action="store_true",
        help="approve the bootstrap prod gates yourself (needs you to be a reviewer)",
    )
    up.add_argument(
        "--reruns", type=int, default=2, help="re-runs of failed jobs per staged run (default 2)"
    )
    up.add_argument("--timeout", type=float, default=60, help="minutes per staged run (default 60)")
    up.add_argument("--keep-workdir", type=Path, help=argparse.SUPPRESS)
    for cmd, text in (
        ("pr", "open a demo pull request"),
        ("status", "show recent runs"),
        ("down", "delete the sandbox repository"),
    ):
        p = sub.add_parser(cmd, help=text)
        p.add_argument("--repo", required=True, metavar="OWNER/NAME")
        if cmd == "pr":
            p.add_argument("--text", help="new marker value (default: demo-<timestamp>)")
    args = parser.parse_args(argv)
    eph = eph or Ephemeral()
    try:
        if shutil.which("gh") is None and eph.run is _run:
            raise EphemeralError("the gh CLI is required (https://cli.github.com)")
        if args.cmd == "up":
            eph.up(
                name=args.name,
                reviewers=args.reviewer,
                private=args.private,
                prevent_self_review=args.prevent_self_review,
                contents_token=env.get("SANDBOX_CONTENTS_TOKEN") or None,
                use_gh_token=args.use_gh_token,
                teams_webhook=env.get("SANDBOX_TEAMS_WEBHOOK") or None,
                approve=args.approve,
                reruns=args.reruns,
                timeout=args.timeout * 60,
                keep_workdir=args.keep_workdir,
            )
        elif args.cmd == "pr":
            eph.pr(args.repo, args.text)
        elif args.cmd == "status":
            eph.status(args.repo)
        else:
            eph.down(args.repo)
    except (EphemeralError, NotASandbox) as exc:
        print(f"ephemeral: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
