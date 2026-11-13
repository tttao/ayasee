"""`ayasee main-tip` (R70): prints the commit at the tip of main, fails closed."""

import httpx
import pytest
import respx

from ayasee import cli, maintip  # noqa: F401  (registers the subcommand)

API = "https://api.github.com"
TIP = "c" * 40


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)


@respx.mock
def test_prints_the_tip_of_main(capsys):
    route = respx.get(f"{API}/repos/acme/infra/commits/main").mock(
        return_value=httpx.Response(200, json={"sha": TIP})
    )
    assert cli.main(["main-tip", "--repo", "acme/infra"]) == 0
    assert capsys.readouterr().out.strip() == TIP
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"


@respx.mock
@pytest.mark.parametrize(
    "response", [httpx.Response(404, json={}), httpx.Response(200, json={"nope": 1})]
)
def test_api_failure_fails_closed(response, capsys):
    respx.get(f"{API}/repos/acme/infra/commits/main").mock(return_value=response)
    assert cli.main(["main-tip", "--repo", "acme/infra"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "main-tip: error" in captured.err


def test_missing_token_fails_closed(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN")
    assert cli.main(["main-tip", "--repo", "acme/infra"]) == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_bad_repo_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main(["main-tip", "--repo", "not a repo"])
    assert exc.value.code == 2


# --- --check SHA (issue #15) -------------------------------------------------------------------

SHA = "a" * 40


def _compare(status="ahead", files=()):
    return httpx.Response(200, json={"status": status, "files": [{"filename": f} for f in files]})


def _check(tmp_path, monkeypatch, config="iac_root: infra\nunits_root: infra/stages\n"):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ayasee.yaml").write_text(config)
    return cli.main(["main-tip", "--repo", "acme/infra", "--check", SHA])


def _tip(sha=TIP):
    return respx.get(f"{API}/repos/acme/infra/commits/main").mock(
        return_value=httpx.Response(200, json={"sha": sha})
    )


@respx.mock
def test_check_at_the_tip_goes_on_silently(tmp_path, monkeypatch, capsys):
    _tip(SHA)
    assert _check(tmp_path, monkeypatch) == 0
    assert capsys.readouterr().out == ""


@respx.mock
@pytest.mark.parametrize(
    "files",
    [["app/main.py"], ["app/x.py", "infra/README.md"], ["docs/infra.md"], []],
    ids=["application", "docs-in-iac-root", "docs", "empty"],
)
def test_check_goes_on_when_later_commits_leave_the_iac_alone(tmp_path, monkeypatch, capsys, files):
    _tip()
    respx.get(f"{API}/repos/acme/infra/compare/{SHA}...{TIP}").mock(
        return_value=_compare(files=files)
    )
    assert _check(tmp_path, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "no later commit changes the IaC" in out and TIP in out


@respx.mock
@pytest.mark.parametrize(
    "files",
    [
        ["infra/stages/prod/dns/terragrunt.hcl"],
        ["infra/root.hcl", "app/main.py"],
        [".ayasee.yaml"],
        [".github/workflows/main.yml"],
        [".github/actions/x/action.yml"],
    ],
    ids=["unit", "root-hcl", "config", "workflow", "local-action"],
)
def test_check_refuses_when_a_later_commit_touches_the_iac(tmp_path, monkeypatch, capsys, files):
    _tip()
    respx.get(f"{API}/repos/acme/infra/compare/{SHA}...{TIP}").mock(
        return_value=_compare(files=files)
    )
    assert _check(tmp_path, monkeypatch) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "main-tip: superseded:" in captured.err


@respx.mock
def test_check_sees_the_old_path_of_a_rename(tmp_path, monkeypatch, capsys):
    _tip()
    respx.get(f"{API}/repos/acme/infra/compare/{SHA}...{TIP}").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ahead",
                "files": [{"filename": "attic/root.hcl", "previous_filename": "infra/root.hcl"}],
            },
        )
    )
    assert _check(tmp_path, monkeypatch) == 1
    assert "superseded" in capsys.readouterr().err


@respx.mock
def test_check_at_the_repository_root_refuses_any_code_change(tmp_path, monkeypatch, capsys):
    _tip()
    respx.get(f"{API}/repos/acme/infra/compare/{SHA}...{TIP}").mock(
        return_value=_compare(files=["app/main.py"])
    )
    assert _check(tmp_path, monkeypatch, config="stages: [dev, uat, prod]\n") == 1
    assert "superseded" in capsys.readouterr().err


@respx.mock
@pytest.mark.parametrize("status", ["diverged", "behind", "identical"])
def test_check_refuses_a_tip_that_does_not_descend(tmp_path, monkeypatch, capsys, status):
    _tip()
    respx.get(f"{API}/repos/acme/infra/compare/{SHA}...{TIP}").mock(
        return_value=_compare(status=status, files=["app/main.py"])
    )
    assert _check(tmp_path, monkeypatch) == 1
    assert "does not descend" in capsys.readouterr().err


@respx.mock
def test_check_refuses_a_list_that_may_be_cut(tmp_path, monkeypatch, capsys):
    _tip()
    respx.get(f"{API}/repos/acme/infra/compare/{SHA}...{TIP}").mock(
        return_value=_compare(files=[f"app/{i}.py" for i in range(300)])
    )
    assert _check(tmp_path, monkeypatch) == 1
    assert "too many files" in capsys.readouterr().err


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={}),
        httpx.Response(200, json={"files": []}),
        httpx.Response(200, json={"status": "ahead", "files": [{"nope": 1}]}),
        httpx.Response(200, json={"status": "ahead", "files": "x"}),
        httpx.Response(200, json={"status": "ahead"}),
    ],
)
def test_check_fails_closed_on_a_bad_compare(tmp_path, monkeypatch, capsys, response):
    _tip()
    respx.get(f"{API}/repos/acme/infra/compare/{SHA}...{TIP}").mock(return_value=response)
    assert _check(tmp_path, monkeypatch) == 1
    assert "main-tip: error" in capsys.readouterr().err


def test_check_fails_closed_on_a_bad_configuration(tmp_path, monkeypatch, capsys):
    assert _check(tmp_path, monkeypatch, config="stages: 3\n") == 1
    assert "main-tip: error" in capsys.readouterr().err


@pytest.mark.parametrize("sha", ["abc", "A" * 40, "g" * 40])
def test_check_needs_a_full_sha(sha):
    with pytest.raises(SystemExit) as exc:
        cli.main(["main-tip", "--repo", "acme/infra", "--check", sha])
    assert exc.value.code == 2
