"""Static checks on the cockpit-collect reusable workflow and its snapshot-changed script."""

import json
import re
import shutil
import subprocess

import pytest
import yaml
from conftest import AYASEE_USES, ROOT, SHA_REF, WORKFLOWS
from conftest import index as _index
from conftest import steps as _steps

PATH = WORKFLOWS / "cockpit-collect.yml"
TEXT = PATH.read_text()
SCRIPT = ROOT / "scripts" / "snapshot-changed.sh"
TOKEN_ACTION = "actions/create-github-app-token@"


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(TEXT)


def _tokens(wf):
    return {s["id"]: s for s in _steps(wf, "collect") if s.get("uses", "").startswith(TOKEN_ACTION)}


def test_concurrency_single_collector(wf):
    assert wf["concurrency"] == {"group": "ayasee-cockpit", "cancel-in-progress": False}


def test_triggers_and_secrets(wf):
    assert list(wf[True]) == ["workflow_call"]
    call = wf[True]["workflow_call"]
    assert set(call["inputs"]) == {
        "renderer",
        "repos-file",
        "data-repository",
        "issue-number",
        "history-days",
        "tool-mirror",
    }
    assert call["inputs"]["repos-file"]["default"] == "repos.yaml"
    assert call["inputs"]["data-repository"]["required"] is True
    assert set(call["secrets"]) == {"app-id", "app-private-key"}
    assert "secrets: inherit" not in TEXT
    assert wf["permissions"] == {}


def test_renderer_input_enum(wf):
    step = _steps(wf, "collect")[0]
    assert step["id"] == "inputs"
    assert step["env"]["RENDERER"] == "${{ inputs.renderer }}"
    assert "pages|issue|summary" in step["run"]
    assert "exit 1" in step["run"]
    # the validation runs before any token is minted
    assert _index(_steps(wf, "collect"), lambda s: s.get("uses", "").startswith(TOKEN_ACTION)) > 0


def test_app_token_read_only_permissions(wf):
    tokens = _tokens(wf)
    assert set(tokens) == {"read-token", "write-token", "issue-token"}
    read = tokens["read-token"]["with"]
    perms = {k: v for k, v in read.items() if k.startswith("permission-")}
    assert perms == {
        f"permission-{p}": "read"
        for p in ("metadata", "actions", "issues", "contents", "deployments", "security-events")
    }
    # R62: narrowed to the repositories listed from repos.yaml, never owner-wide
    assert read["repositories"] == "${{ steps.repos.outputs.list }}"
    assert "zizmor: ignore" not in TEXT


def test_read_token_repositories_come_from_the_validated_list(wf):
    st = _steps(wf, "collect")
    names = [s.get("id") for s in st]
    # after the data checkout (repos.yaml) and the setup action (the CLI), before the read token
    co = _index(st, lambda s: s.get("with", {}).get("path") == "data")
    setup = _index(st, lambda s: s.get("uses", "").startswith("OWNER/ayasee/actions/setup@"))
    repos, read = names.index("repos"), names.index("read-token")
    assert co < setup < repos < read
    run = st[repos]["run"]
    assert 'ayasee cockpit repos --repos-file "data/$REPOS_FILE" --owner "$OWNER"' in run
    assert "${{" not in run
    assert st[repos]["env"]["OWNER"] == "${{ steps.inputs.outputs.owner }}"
    # no step before the list may hold the read token
    assert "read-token" not in "".join(str(s) for s in st[:read])


def test_write_token_scoped_to_one_repository(wf):
    write = _tokens(wf)["write-token"]["with"]
    assert write["repositories"] == "${{ steps.inputs.outputs.name }}"
    assert "," not in write["repositories"]
    assert write["permission-contents"] == "write"
    assert set(k for k in write if k.startswith("permission-")) == {"permission-contents"}


def test_issue_renderer_has_its_own_issues_only_token(wf):
    tok = _tokens(wf)["issue-token"]
    assert tok["if"] == "${{ inputs.renderer == 'issue' }}"
    w = tok["with"]
    assert w["repositories"] == "${{ steps.inputs.outputs.name }}"
    assert {k: v for k, v in w.items() if k.startswith("permission-")} == {
        "permission-issues": "write"
    }
    issue = next(s for s in _steps(wf, "collect") if "--issue-number" in s.get("run", ""))
    assert issue["env"]["GITHUB_TOKEN"] == "${{ steps.issue-token.outputs.token }}"


def test_collect_job_holds_no_write_permissions(wf):
    assert wf["jobs"]["collect"]["permissions"] == {"contents": "read"}


def test_every_uses_is_sha_or_owner_pinned():
    uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)", TEXT, re.M)
    assert uses
    for u in uses:
        assert SHA_REF.search(u) or AYASEE_USES.match(u), u
    assert "OWNER/ayasee/actions/setup@" + "0" * 40 in TEXT


def test_no_uv_cache():
    assert "enable-cache: true" not in TEXT
    assert "astral-sh/setup-uv" not in TEXT  # the setup action owns uv and disables its cache
    assert not re.search(r"(?<!manager-)cache:\s*(true|pip|npm|uv)", TEXT)


def test_inputs_reach_scripts_only_through_env():
    for run in re.findall(r"run: \|?\s*\n?(.*?)(?=\n\s+- |\n\s+\w[\w-]*:|\Z)", TEXT, re.S):
        assert "${{ inputs." not in run and "${{ secrets." not in run


def test_collect_steps_order_and_cache(wf):
    st = _steps(wf, "collect")
    restore = _index(st, lambda s: s.get("uses", "").startswith("actions/cache/restore@"))
    collect = _index(st, lambda s: "ayasee cockpit collect" in s.get("run", ""))
    commit = _index(st, lambda s: "git -C data commit" in s.get("run", ""))
    save = _index(st, lambda s: s.get("uses", "").startswith("actions/cache/save@"))
    assert restore < collect < commit < save
    r = st[restore]["with"]
    assert r["path"] == "${{ env.SNAPSHOT_CACHE }}"
    assert wf["jobs"]["collect"]["env"]["SNAPSHOT_CACHE"] == ".ayasee/etag-cache.json"
    prefix = "ayasee-cockpit-etag-${{ inputs.data-repository }}-"
    assert r["key"] == prefix + "${{ github.run_id }}"
    assert r["restore-keys"] == prefix
    # the save key carries the content hash and is skipped when it equals the restored key
    assert st[save]["with"]["key"] == prefix + "${{ hashFiles(env.SNAPSHOT_CACHE) }}"
    assert st[save]["with"]["path"] == r["path"]
    cond = st[save]["if"]
    assert "cache-matched-key" in cond and "hashFiles(env.SNAPSHOT_CACHE)" in cond
    c = st[collect]
    assert c["env"]["GITHUB_TOKEN"] == "${{ steps.read-token.outputs.token }}"
    for flag in ("--repos-file", "--etag-cache", "--out snapshot.json"):
        assert flag in c["run"]
    # a failed collect (exit 1) stops the job before any commit: no continue-on-error anywhere
    assert "continue-on-error" not in TEXT
    assert "always()" not in TEXT
    # the save step is reachable only after a successful collect: no status function, no if
    assert not re.search(r"always\(|failure\(|cancelled\(|success\(", st[save]["if"])
    assert "failure()" not in TEXT and "cancelled()" not in TEXT


def test_commit_only_when_changed_as_bot(wf):
    st = _steps(wf, "collect")
    diff = next(s for s in st if s.get("id") == "diff")
    assert "snapshot-changed.sh" in diff["run"]
    write = next(s for s in st if "history" in s.get("run", "") and "cp snapshot.json" in s["run"])
    commit = next(s for s in st if "git -C data commit" in s.get("run", ""))
    for s in (write, commit):
        assert s["if"] == "${{ steps.diff.outputs.result == 'changed' }}"
    assert "date -u +%Y/%m/%d" in write["run"] and "date -u +%H%M" in write["run"]
    assert "ayasee-cockpit[bot]" in commit["run"]
    co = next(
        s
        for s in st
        if s.get("uses", "").startswith("actions/checkout@")
        and "path" in s["with"]
        and s["with"]["path"] == "data"
    )
    assert co["with"]["persist-credentials"] is False
    assert co["with"]["token"] == "${{ steps.write-token.outputs.token }}"


def test_renderers_match_the_render_md_cli(wf):
    st = _steps(wf, "collect")
    summary = next(s for s in st if "GITHUB_STEP_SUMMARY" in s.get("run", ""))
    assert summary["if"] == "${{ inputs.renderer == 'summary' }}"
    assert (
        summary["run"]
        == 'ayasee cockpit render-md --snapshot snapshot.json --summary "$GITHUB_STEP_SUMMARY"'
    )
    assert ">>" not in summary["run"]
    issue = next(s for s in st if "--issue-number" in s.get("run", ""))
    assert issue["if"] == "${{ inputs.renderer == 'issue' }}"
    assert issue["env"]["DATA_REPOSITORY"] == "${{ inputs.data-repository }}"
    run = " ".join(issue["run"].replace("\\\n", " ").split())
    assert 'render-md --snapshot snapshot.json --issue-number "$ISSUE_NUMBER"' in run
    assert '--repo "$DATA_REPOSITORY"' in run
    assert "lands with Task 21" not in TEXT


def test_history_days_is_validated(wf):
    call = wf[True]["workflow_call"]["inputs"]["history-days"]
    assert call["default"] == "30"
    step = _steps(wf, "collect")[0]
    assert step["env"]["HISTORY_DAYS"] == "${{ inputs.history-days }}"
    assert "history-days must be an integer between 1 and 365" in step["run"]
    assert "-le 365" in step["run"] and "[1-9]" in step["run"]


def _run(cmd, **env):
    import os

    return subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, env={**os.environ, **env}
    )


@pytest.mark.parametrize(
    ("days", "ok"),
    [
        ("1", True),
        ("30", True),
        ("365", True),
        ("0", False),
        ("366", False),
        ("", False),
        ("7d", False),
        ("-1", False),
        ("1000", False),
        ("030", False),
    ],
)
def test_history_days_validation_behaviour(wf, days, ok):
    run = _steps(wf, "collect")[0]["run"]
    r = _run(
        run,
        RENDERER="pages",
        DATA_REPOSITORY="acme/data",
        REPOS_FILE="repos.yaml",
        ISSUE_NUMBER="",
        HISTORY_DAYS=days,
        GITHUB_OUTPUT="/dev/null",
    )
    assert (r.returncode == 0) is ok, r.stdout + r.stderr


def test_history_index_runs_before_upload_and_is_not_committed(wf):
    st = _steps(wf, "collect")
    idx = _index(st, lambda s: "cockpit history-index" in s.get("run", ""))
    upload = _index(st, lambda s: s.get("uses", "").startswith("actions/upload-artifact@"))
    commit = _index(st, lambda s: "git -C data commit" in s.get("run", ""))
    collect = _index(st, lambda s: "ayasee cockpit collect" in s.get("run", ""))
    assert collect < commit < idx < upload  # generated after the commit: never committed
    s = st[idx]
    assert s["if"] == "${{ inputs.renderer == 'pages' }}"
    assert s["env"]["HISTORY_DAYS"] == "${{ inputs.history-days }}"
    run = " ".join(s["run"].replace("\\\n", " ").split())
    assert '--dir data/history --days "$HISTORY_DAYS" --out data/history/index.json' in run
    assert "${{" not in s["run"]


def test_pages_job_uses_the_site_action_with_data_at_the_root(wf):
    pages = wf["jobs"]["pages"]
    assert pages["if"] == "${{ inputs.renderer == 'pages' }}"
    assert pages["needs"] == "collect"
    assert pages["permissions"] == {"pages": "write", "id-token": "write"}
    assert pages["environment"]["name"] == "github-pages"
    steps = pages["steps"]
    uses = [s.get("uses", "") for s in steps]
    site = next(s for s in steps if "actions/cockpit-site@" in s.get("uses", ""))
    assert site["uses"] == "OWNER/ayasee/actions/cockpit-site@" + "0" * 40
    assert "actions/cockpit-site@" + "0" * 40 + " # AYASEE_REF" in TEXT
    assert site["with"] == {
        "snapshot": "cockpit-data/snapshot.json",
        "history-dir": "cockpit-data/history",
        "out": "site",
    }
    up = next(s for s in steps if s.get("uses", "").startswith("actions/upload-pages-artifact@"))
    assert up["with"]["path"] == "site"  # the action puts snapshot.json and history/ at its root
    assert any(u.startswith("actions/deploy-pages@") for u in uses)
    order = [uses.index(site["uses"]), uses.index(up["uses"])]
    assert order == sorted(order)
    assert "dist/data" not in TEXT
    assert not re.search(r"npm (ci|run|install)", TEXT)
    assert "setup-node" not in TEXT


def test_site_action_layout_matches_the_workflow():
    action = yaml.safe_load((ROOT / "actions" / "cockpit-site" / "action.yml").read_text())
    assert {"snapshot", "out", "history-dir"} <= set(action["inputs"])
    text = (ROOT / "actions" / "cockpit-site" / "action.yml").read_text()
    assert '"$SG_OUT/snapshot.json"' in text and '"$SG_OUT/history/index.json"' in text


def test_set_ayasee_ref_rewrites_the_site_checkout(tmp_path):
    script = ROOT / "scripts" / "set-ayasee-ref.sh"
    local = tmp_path / "local.yml"
    shutil.copy(PATH, local)
    subprocess.run([script, "--local", local], check=True)
    text = local.read_text()
    assert "repository: ${{ github.repository }} # AYASEE_REPO" in text
    assert "ref: ${{ github.sha }} # AYASEE_SHA" in text
    assert "OWNER/ayasee" not in text
    assert "./actions/cockpit-site # AYASEE_REF" in text
    head = subprocess.run(
        ["git", "-C", ROOT, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    rel = tmp_path / "rel.yml"
    shutil.copy(PATH, rel)
    subprocess.run([script, "acme/ayasee", head, rel], check=True)
    text = rel.read_text()
    assert "repository: acme/ayasee # AYASEE_REPO" in text
    assert f"ref: {head} # AYASEE_SHA" in text
    assert f"acme/ayasee/actions/setup@{head} # AYASEE_REF" in text
    assert f"acme/ayasee/actions/cockpit-site@{head} # AYASEE_REF" in text


def _changed(tmp_path, old, new):
    n = tmp_path / "new.json"
    n.write_text(json.dumps(new))
    args = [SCRIPT]
    if old is not None:
        o = tmp_path / "old.json"
        o.write_text(json.dumps(old))
        args.append(o)
    else:
        args.append(tmp_path / "absent.json")
    args.append(n)
    return subprocess.run(args, capture_output=True, text=True)


def test_snapshot_changed_ignores_generated_at(tmp_path):
    base = {"schema": "s", "repos": [{"repo": "a/b", "envs": []}]}
    r = _changed(tmp_path, {**base, "generated_at": "T1"}, {**base, "generated_at": "T2"})
    assert (r.returncode, r.stdout.strip()) == (0, "unchanged")
    # key order does not matter either
    r = _changed(tmp_path, {"generated_at": "T1", **base}, {**base, "generated_at": "T2"})
    assert r.stdout.strip() == "unchanged"


def test_snapshot_changed_detects_content_and_missing_file(tmp_path):
    old = {"generated_at": "T1", "repos": [{"repo": "a/b"}]}
    r = _changed(tmp_path, old, {"generated_at": "T1", "repos": [{"repo": "a/c"}]})
    assert (r.returncode, r.stdout.strip()) == (0, "changed")
    r = _changed(tmp_path, None, old)
    assert (r.returncode, r.stdout.strip()) == (0, "changed")


def test_snapshot_changed_rejects_invalid_new_snapshot(tmp_path):
    bad = tmp_path / "new.json"
    bad.write_text("{")
    r = subprocess.run([SCRIPT, tmp_path / "x.json", bad], capture_output=True, text=True)
    assert r.returncode == 2


def test_snapshot_changed_status_is_not_swallowed(wf):
    diff = next(s for s in _steps(wf, "collect") if s.get("id") == "diff")
    assert 'echo "result=$(' not in diff["run"]
    assert re.search(r'^\s*result="\$\(.*snapshot-changed\.sh', diff["run"], re.M)


def test_site_gets_the_fresh_snapshot_not_the_committed_one(wf):
    st = _steps(wf, "collect")
    stage = next(s for s in st if "site-data" in s.get("run", ""))
    assert stage["if"] == "${{ inputs.renderer == 'pages' }}"
    assert "cp snapshot.json site-data/snapshot.json" in stage["run"]
    assert not re.search(r"(?<![-\w])data/snapshot\.json", stage["run"])
    assert "cp -R data/history site-data/history" in stage["run"]
    idx = _index(st, lambda s: "cockpit history-index" in s.get("run", ""))
    up = _index(st, lambda s: s.get("uses", "").startswith("actions/upload-artifact@"))
    assert idx < st.index(stage) < up
    assert st[up]["with"]["path"] == "site-data"  # nothing else (not data/snapshot.json) goes up


def _assemble_step():
    action = yaml.safe_load((ROOT / "actions" / "cockpit-site" / "action.yml").read_text())
    return next(
        s for s in action["runs"]["steps"] if s.get("name") == "Assemble the output directory"
    )


def _assemble(tmp_path, index, files):
    site = tmp_path / "site"
    (site / "dist").mkdir(parents=True)
    (site / "dist" / "index.html").write_text("<html>")
    hist = tmp_path / "data" / "history"
    hist.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        f = hist / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    (hist / "index.json").write_text(json.dumps(index))
    (tmp_path / "snap.json").write_text("{}")
    out = tmp_path / "out"
    r = _run(
        _assemble_step()["run"],
        SG_SITE_DIR=str(site),
        SG_SNAPSHOT=str(tmp_path / "snap.json"),
        SG_OUT=str(out),
        SG_HISTORY_DIR=str(hist),
    )
    return r, out


def test_site_action_publishes_only_the_index_and_listed_files(tmp_path):
    ok = "2026/11/06/0900.json"
    r, out = _assemble(
        tmp_path,
        ["history/" + ok, "history/../../etc/x.json", "history/2026/11/06/9999.json"],
        {ok: "{}", "x.html": "<script>", "2026/11/06/0800.json": "{}", "2026/11/06/evil.html": "x"},
    )
    assert r.returncode == 0, r.stderr
    published = sorted(
        p.relative_to(out).as_posix() for p in (out / "history").rglob("*") if p.is_file()
    )
    assert published == sorted(["history/index.json", "history/" + ok])
    assert (out / "snapshot.json").read_text() == "{}"


def test_site_action_rejects_history_without_a_valid_index(tmp_path):
    r, _ = _assemble(tmp_path, {"not": "a list"}, {})
    assert r.returncode != 0
    r, out = _assemble(tmp_path / "e", [], {})
    assert r.returncode == 0 and sorted(p.name for p in (out / "history").iterdir()) == [
        "index.json"
    ]
