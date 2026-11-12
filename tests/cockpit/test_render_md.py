import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from ayasee import cli
from ayasee.cockpit.render_md import (
    MARKER,
    build_history_index,
    escape_md,
    merge_env_order,
    render,
    status_for,
)

FIXTURES = Path(__file__).parents[2] / "schema" / "fixtures"
BASE = "https://ghes.example/api/v3"


def _env(name="dev", **kw):
    e = {
        "env": name,
        "live_sha": "a1b2c3d4e5f6a7b8",
        "live_at": "2026-11-06T08:00:00Z",
        "converged": True,
        "pending_commits": 0,
        "pending_compare_url": None,
        "gate": {"waiting": False, "since": None, "run_url": None},
        "drift": {"open": False, "since": None, "issue_url": None, "resources": 0},
        "health": {"last_result": "applied", "stop_the_line": False},
    }
    e.update(kw)
    return e


def _snap(repos):
    return {
        "schema": "ayasee.snapshot/v1",
        "generated_at": "2026-11-06T09:00:00Z",
        "repos": repos,
    }


def _repo(name, envs, **kw):
    r = {
        "repo": name,
        "main_sha": "f" * 40,
        "findings": {
            "critical": 1,
            "high": 2,
            "url": "https://github.com/acme/x/security/code-scanning",
        },
        "envs": envs,
    }
    r.update(kw)
    return r


# ---- shared fixtures -------------------------------------------------------------------------

_PREC = json.loads((FIXTURES / "status-precedence.json").read_text())


@pytest.mark.parametrize("case", _PREC["cases"], ids=lambda c: c["name"])
def test_md_same_status_precedence_as_site(case):
    i = case["in"]
    env = _env(
        live_sha=i["live_sha"],
        pending_commits=i["pending_commits"],
        gate={"waiting": i["waiting"], "since": None, "run_url": None},
        drift={"open": i["drift_open"], "since": None, "issue_url": None, "resources": 0},
        health={"last_result": i["last_result"], "stop_the_line": i["stop_the_line"]},
    )
    assert status_for(env) == case["expect"]


def test_precedence_fixture_order_matches_code():
    from ayasee.cockpit.render_md import PRECEDENCE

    assert [name for name, _ in PRECEDENCE] == _PREC["order"]


_ORDER = json.loads((FIXTURES / "env-order.json").read_text())


@pytest.mark.parametrize("case", _ORDER["cases"], ids=lambda c: c["name"])
def test_md_env_order_matches_site(case):
    assert merge_env_order(case["in"]) == case["expect"]


# ---- rendering -------------------------------------------------------------------------------


def test_table_shape_cells_and_footer():
    snap = _snap(
        [
            _repo(
                "acme/lake",
                [
                    _env("dev"),
                    _env(
                        "prod",
                        pending_commits=3,
                        pending_compare_url="https://github.com/acme/lake/compare/a...b",
                    ),
                ],
            ),
            _repo(
                "acme/ml", [_env("dev", health={"last_result": "failed", "stop_the_line": False})]
            ),
        ]
    )
    md = render(snap)
    lines = md.splitlines()
    assert "2026-11-06T09:00:00Z" in lines[0]
    assert "| Repository | dev | prod |" in md
    compare = "https://github.com/acme/lake/compare/a...b"
    assert f"| acme/lake | Synced a1b2c3d | [Pending a1b2c3d (+3)]({compare}) |" in md
    assert "| acme/ml | Failed a1b2c3d | — |" in md
    assert "acme/lake: critical 1, high 2" in md
    assert "https://github.com/acme/x/security/code-scanning" in md
    assert not re.search(r"[^\x00-\x7e—·]", md)  # no emoji or other symbols


def test_missing_sha_and_unknown_and_findings_null():
    snap = _snap(
        [
            _repo(
                "acme/new",
                [
                    _env(
                        "dev",
                        live_sha=None,
                        health={"last_result": "unknown", "stop_the_line": False},
                    )
                ],
                findings={"critical": None, "high": None, "url": None},
            )
        ]
    )
    md = render(snap)
    assert "| acme/new | Unknown - |" in md
    assert "acme/new: critical n/a, high n/a" in md


def test_repo_error_row():
    snap = _snap([_repo("acme/broken", [], error="ledger branch not readable (403)")])
    md = render(snap)
    assert "acme/broken" in md
    assert "ledger branch not readable \\(403\\)" in md or "ledger branch not readable (403)" in md


def test_escapes_markdown_and_html_in_names():
    nasty = "a|b`c<d>e*f_g[h]i\nj\\k&l"
    out = escape_md(nasty)
    assert "\n" not in out
    for ch in "|`*_[]":
        assert f"\\{ch}" in out
    assert "<" not in out and ">" not in out
    assert "&lt;" in out and "&gt;" in out and "&amp;" in out


def test_xss_ish_names_cannot_inject_markup_or_extra_columns():
    evil = "<img src=x onerror=alert(1)>|[x](javascript:alert(1))`"
    snap = _snap(
        [
            _repo(
                f"acme/{evil}",
                [_env(evil)],
                findings={"critical": 0, "high": 0, "url": "javascript:alert(1)"},
            )
        ]
    )
    md = render(snap)
    assert "<img" not in md and "<script" not in md
    assert not re.search(r"(?<!\\)\]\(javascript", md)  # no live link
    assert "javascript:alert" not in md.replace(escape_md(evil), "")
    for line in md.splitlines():
        if line.startswith("|"):
            assert len(re.findall(r"(?<!\\)\|", line)) == 3  # | repo | env |


def test_only_https_links():
    snap = _snap(
        [
            _repo(
                "acme/x",
                [_env("dev", pending_commits=1, pending_compare_url="http://plain.example/c")],
                findings={"critical": 0, "high": 0, "url": "http://plain.example/s"},
            )
        ]
    )
    md = render(snap)
    assert "http://" not in md
    assert "](" not in md


def test_link_parentheses_and_spaces_cannot_break_out():
    snap = _snap(
        [
            _repo(
                "acme/x",
                [
                    _env(
                        "dev",
                        pending_commits=1,
                        pending_compare_url="https://github.com/a/b/c) evil (",
                    )
                ],
            )
        ]
    )
    md = render(snap)
    assert ") evil (" not in md


# ---- history index ---------------------------------------------------------------------------


def _touch(base: Path, rel: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")


def test_history_index_window_sorting_and_relativity(tmp_path):
    hist = tmp_path / "data" / "history"
    for rel in [
        "2026/11/06/0900.json",
        "2026/11/01/2359.json",
        "2026/10/20/1200.json",  # outside 7 days
        "2026/11/05/1030.json",
        "2026/11/06/0100.json",
        "2026/11/06/notes.txt",
        "index.json",
        "2026/13/40/9999.json",  # not a real date
    ]:
        _touch(hist, rel)
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    out = build_history_index(hist, 7, now)
    # relative to DIR's parent, so a site that serves the parent can fetch them
    assert out == [
        "history/2026/11/01/2359.json",
        "history/2026/11/05/1030.json",
        "history/2026/11/06/0100.json",
        "history/2026/11/06/0900.json",
    ]


def test_history_index_boundary_is_inclusive(tmp_path):
    hist = tmp_path / "history"
    _touch(hist, "2026/10/30/1200.json")
    _touch(hist, "2026/10/30/1159.json")
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    assert build_history_index(hist, 7, now) == ["history/2026/10/30/1200.json"]


def test_history_index_cli(tmp_path, capsys):
    hist = tmp_path / "history"
    now = datetime.now(UTC)
    rel = now.strftime("%Y/%m/%d/%H%M.json")
    _touch(hist, rel)
    _touch(hist, "2020/01/01/0000.json")
    out = hist / "index.json"
    rc = cli.main(
        ["cockpit", "history-index", "--dir", str(hist), "--days", "3", "--out", str(out)]
    )
    assert rc == 0
    assert json.loads(out.read_text()) == [f"history/{rel}"]


def test_history_index_cli_missing_dir(tmp_path):
    rc = cli.main(
        [
            "cockpit",
            "history-index",
            "--dir",
            str(tmp_path / "nope"),
            "--days",
            "3",
            "--out",
            str(tmp_path / "i.json"),
        ]
    )
    assert rc == 2


# ---- CLI render-md ---------------------------------------------------------------------------


@pytest.fixture
def snap_file(tmp_path):
    p = tmp_path / "snapshot.json"
    p.write_text(json.dumps(_snap([_repo("acme/lake", [_env("dev")])])))
    return p


def test_cli_stdout(snap_file, capsys):
    assert cli.main(["cockpit", "render-md", "--snapshot", str(snap_file)]) == 0
    out = capsys.readouterr().out
    assert "| acme/lake |" in out
    assert MARKER not in out


def test_cli_summary_appends(snap_file, tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("existing\n")
    for _ in range(2):
        assert (
            cli.main(
                ["cockpit", "render-md", "--snapshot", str(snap_file), "--summary", str(summary)]
            )
            == 0
        )
    text = summary.read_text()
    assert text.startswith("existing\n")
    assert text.count("| acme/lake |") == 2


def test_cli_bad_snapshot(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"schema": "other"}))
    assert cli.main(["cockpit", "render-md", "--snapshot", str(p)]) == 2
    assert cli.main(["cockpit", "render-md", "--snapshot", str(tmp_path / "missing.json")]) == 2


@pytest.fixture
def gh_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_API_URL", BASE)
    monkeypatch.delenv("AYASEE_DATA_REPOSITORY", raising=False)


@respx.mock
def test_issue_mode_updates_body_with_marker_first(snap_file, gh_env):
    respx.get(f"{BASE}/repos/acme/data/issues/5").mock(
        return_value=httpx.Response(200, json={"body": f"{MARKER}\nold table"})
    )
    patch = respx.patch(f"{BASE}/repos/acme/data/issues/5").mock(
        return_value=httpx.Response(200, json={})
    )
    rc = cli.main(
        [
            "cockpit",
            "render-md",
            "--snapshot",
            str(snap_file),
            "--issue-number",
            "5",
            "--repo",
            "acme/data",
        ]
    )
    assert rc == 0
    body = json.loads(patch.calls[0].request.content)["body"]
    assert body.startswith(MARKER + "\n")
    assert "| acme/lake |" in body
    assert set(json.loads(patch.calls[0].request.content)) == {"body"}


@respx.mock
def test_issue_mode_accepts_empty_body(snap_file, gh_env):
    respx.get(f"{BASE}/repos/acme/data/issues/5").mock(
        return_value=httpx.Response(200, json={"body": None})
    )
    patch = respx.patch(f"{BASE}/repos/acme/data/issues/5").mock(
        return_value=httpx.Response(200, json={})
    )
    rc = cli.main(
        [
            "cockpit",
            "render-md",
            "--snapshot",
            str(snap_file),
            "--issue-number",
            "5",
            "--repo",
            "acme/data",
        ]
    )
    assert rc == 0
    assert patch.called


@respx.mock
def test_issue_mode_refuses_a_human_issue(snap_file, gh_env, capsys):
    respx.get(f"{BASE}/repos/acme/data/issues/5").mock(
        return_value=httpx.Response(200, json={"body": "Please look at this outage"})
    )
    patch = respx.patch(f"{BASE}/repos/acme/data/issues/5").mock(
        return_value=httpx.Response(200, json={})
    )
    rc = cli.main(
        [
            "cockpit",
            "render-md",
            "--snapshot",
            str(snap_file),
            "--issue-number",
            "5",
            "--repo",
            "acme/data",
        ]
    )
    assert rc == 2
    assert not patch.called
    assert "marker" in capsys.readouterr().err


@respx.mock
def test_issue_mode_repo_from_env(snap_file, gh_env, monkeypatch):
    monkeypatch.setenv("AYASEE_DATA_REPOSITORY", "acme/data")
    respx.get(f"{BASE}/repos/acme/data/issues/7").mock(
        return_value=httpx.Response(200, json={"body": ""})
    )
    patch = respx.patch(f"{BASE}/repos/acme/data/issues/7").mock(
        return_value=httpx.Response(200, json={})
    )
    assert (
        cli.main(["cockpit", "render-md", "--snapshot", str(snap_file), "--issue-number", "7"]) == 0
    )
    assert patch.called


def test_issue_mode_needs_a_repo(snap_file, gh_env):
    assert (
        cli.main(["cockpit", "render-md", "--snapshot", str(snap_file), "--issue-number", "7"]) == 2
    )


def test_labels_are_listed_per_repository():
    from ayasee.cockpit.render_md import render

    snap = {
        "schema": "ayasee.snapshot/v1",
        "generated_at": "2026-11-12T00:00:00Z",
        "repos": [
            {
                "repo": "a/one",
                "main_sha": None,
                "findings": {},
                "envs": [],
                "labels": [
                    {"kind": "non-compliant", "setting": "solo_mode"},
                    {"kind": "deviation", "setting": "identity"},
                ],
            },
            {"repo": "a/two", "main_sha": None, "findings": {}, "envs": [], "labels": []},
            {"repo": "a/three", "main_sha": None, "findings": {}, "envs": []},
        ],
    }
    text = render(snap)
    assert "- a/one: solo mode (NON-COMPLIANT), secrets identity (DEVIATION)" in text
    assert "a/two:" not in text.split("Labels")[1] and "a/three:" not in text.split("Labels")[1]
    snap["repos"][0]["labels"] = []
    assert "Labels" not in render(snap)
