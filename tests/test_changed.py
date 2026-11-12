import subprocess
from pathlib import Path

import pytest

from ayasee import cli
from ayasee.changed import ALL, changed_units, classify


def _is_unit(root: Path):
    return lambda rel: (root / rel / "terragrunt.hcl").is_file()


def _repo(tmp_path: Path) -> tuple[Path, callable]:
    def git(*a):
        return subprocess.run(
            ["git", "-C", tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", *a],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    for rel in ("stages/dev/app", "stages/dev/net/vnet", "stages/uat/app"):
        (tmp_path / rel).mkdir(parents=True)
        (tmp_path / rel / "terragrunt.hcl").write_text("")
        (tmp_path / rel / "main.tf").write_text("a")
    (tmp_path / "README.md").write_text("x")
    git("add", "-A")
    git("commit", "-qm", "base")
    return tmp_path, git


def _commit(git, repo: Path, edits: dict[str, str | None]) -> tuple[str, str]:
    base = git("rev-parse", "HEAD")
    for rel, text in edits.items():
        f = repo / rel
        if text is None:
            f.unlink()
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
    git("add", "-A")
    git("commit", "-qm", "change")
    return base, git("rev-parse", "HEAD")


def test_classify_maps_files_to_units(tmp_path):
    (tmp_path / "stages/dev/net/vnet").mkdir(parents=True)
    (tmp_path / "stages/dev/net/vnet/terragrunt.hcl").write_text("")
    (tmp_path / "stages/dev/app").mkdir(parents=True)
    (tmp_path / "stages/dev/app/terragrunt.hcl").write_text("")
    files = ["stages/dev/net/vnet/sub/x.tf", "stages/dev/app/main.tf", "stages/dev/app/b.tf"]
    got = classify(files, "stages", "dev", _is_unit(tmp_path))
    assert got == ["app", "net__vnet"]


@pytest.mark.parametrize(
    "files",
    [
        ["modules/x/main.tf"],
        ["stages/dev/app/main.tf", "root.hcl"],
        ["stages/uat/app/main.tf"],
        ["stages/dev/orphan/main.tf"],  # no unit above it
        ["stages/dev/common.hcl"],  # directly under the environment
        ["stages/devx/app/main.tf"],
    ],
)
def test_classify_returns_all_for_anything_else(tmp_path, files):
    (tmp_path / "stages/dev/app").mkdir(parents=True)
    (tmp_path / "stages/dev/app/terragrunt.hcl").write_text("")
    assert classify(files, "stages", "dev", _is_unit(tmp_path)) is ALL


def test_classify_ignores_documentation(tmp_path):
    (tmp_path / "stages/dev/app").mkdir(parents=True)
    (tmp_path / "stages/dev/app/terragrunt.hcl").write_text("")
    isu = _is_unit(tmp_path)
    assert classify(["README.md", "docs/a/b.txt", "LICENSE", "x/y.md"], "stages", "dev", isu) == []
    # mixed: the docs do not hide or widen the code change
    assert classify(["docs/a/b.txt", "stages/dev/app/main.tf"], "stages", "dev", isu) == ["app"]
    assert classify(["README.md", "modules/x/main.tf"], "stages", "dev", isu) is ALL
    assert classify(["notdocs/a.txt"], "stages", "dev", isu) is ALL


def test_classify_no_files_is_empty(tmp_path):
    assert classify([], "stages", "dev", _is_unit(tmp_path)) == []


def test_changed_units_on_a_real_repo(tmp_path, monkeypatch):
    repo, git = _repo(tmp_path)
    monkeypatch.chdir(repo)
    base, head = _commit(
        git, repo, {"stages/dev/app/main.tf": "b", "stages/dev/net/vnet/n.tf": "n"}
    )
    assert changed_units(base, head, "stages", "dev") == ["app", "net__vnet"]
    base, head = _commit(git, repo, {"modules/m.tf": "m"})
    assert changed_units(base, head, "stages", "dev") is ALL
    base, head = _commit(
        git, repo, {"stages/dev/app/terragrunt.hcl": None, "stages/dev/app/main.tf": None}
    )
    assert changed_units(base, head, "stages", "dev") is ALL  # a removed unit: plan everything
    assert changed_units(head, head, "stages", "dev") == []


def test_changed_units_uncomputable_diff_is_all(tmp_path, monkeypatch):
    repo, git = _repo(tmp_path)
    monkeypatch.chdir(repo)
    head = git("rev-parse", "HEAD")
    assert changed_units("f" * 40, head, "stages", "dev") is ALL
    assert changed_units("--output=x", head, "stages", "dev") is ALL
    monkeypatch.chdir(tmp_path.parent)  # not a repository
    assert changed_units(head, head, "stages", "dev") is ALL


def test_cli_prints_comma_list_or_all(tmp_path, monkeypatch, capsys):
    repo, git = _repo(tmp_path)
    monkeypatch.chdir(repo)
    base, head = _commit(git, repo, {"stages/dev/app/main.tf": "b"})
    args = [
        "changed-units",
        "--base",
        base,
        "--head",
        head,
        "--units-root",
        "stages",
        "--env",
        "dev",
    ]
    assert cli.main(args) == 0
    assert capsys.readouterr().out == "app\n"
    base, head = _commit(git, repo, {"README.md": "y"})
    assert cli.main(["changed-units", *args[1:2], base, *args[3:4], head, *args[5:]]) == 0
    assert capsys.readouterr().out == "\n"  # docs only: nothing to plan
    base, head = _commit(git, repo, {"README.md": "z", "modules/m.tf": "m"})
    assert cli.main(["changed-units", *args[1:2], base, *args[3:4], head, *args[5:]]) == 0
    assert capsys.readouterr().out == "ALL\n"
    assert cli.main(["changed-units", *args[1:2], head, *args[3:4], head, *args[5:]]) == 0
    assert capsys.readouterr().out == "\n"


def test_classify_ignores_files_outside_the_iac_root(tmp_path):
    (tmp_path / "infra/iac/stages/dev/app").mkdir(parents=True)
    (tmp_path / "infra/iac/stages/dev/app/terragrunt.hcl").write_text("")
    isu = _is_unit(tmp_path)

    def cls(files):
        return classify(files, "infra/iac/stages", "dev", isu, iac_root="infra/iac")

    # application code beside the estate never changes infrastructure
    assert cls(["server/src/a.py", "web/Dockerfile", ".github/workflows/app.yml"]) == []
    assert cls(["server/src/a.py", "infra/iac/stages/dev/app/main.tf"]) == ["app"]
    # inside the IaC root, the fail-safe stays: modules, root.hcl, other stages
    assert cls(["server/src/a.py", "infra/iac/modules/m/main.tf"]) is ALL
    assert cls(["infra/iac/root.hcl"]) is ALL
    assert cls(["infra/iac/stages/prod/app/main.tf"]) is ALL
    # a sibling directory that only shares the prefix is outside
    assert cls(["infra/iacx/main.tf"]) == []
    # the configuration file always widens, wherever it lives
    assert cls([".ayasee.yaml"]) is ALL
    assert (
        classify(
            ["cfg/ayasee.yaml"],
            "infra/iac/stages",
            "dev",
            isu,
            iac_root="infra/iac",
            config_path="cfg/ayasee.yaml",
        )
        is ALL
    )
    assert (
        classify(
            ["cfg/ayasee.yaml"],
            "infra/iac/stages",
            "dev",
            isu,
            iac_root="infra/iac",
            config_path="./cfg/ayasee.yaml",
        )
        is ALL
    )


def test_default_iac_root_keeps_the_whole_repository_in_scope(tmp_path):
    (tmp_path / "stages/dev/app").mkdir(parents=True)
    (tmp_path / "stages/dev/app/terragrunt.hcl").write_text("")
    assert classify(["server/src/a.py"], "stages", "dev", _is_unit(tmp_path)) is ALL
    assert classify(["server/src/a.py"], "stages", "dev", _is_unit(tmp_path), iac_root=".") is ALL


def test_cli_accepts_iac_root_and_config_path(tmp_path, monkeypatch, capsys):
    def git(*a):
        return subprocess.run(
            ["git", "-C", tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", *a],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    unit = tmp_path / "infra/iac/stages/dev/app"
    unit.mkdir(parents=True)
    (unit / "terragrunt.hcl").write_text("")
    (tmp_path / "app.py").write_text("a")
    git("add", "-A")
    git("commit", "-qm", "base")
    monkeypatch.chdir(tmp_path)
    base, head = _commit(git, tmp_path, {"app.py": "b"})
    common = ["--units-root", "infra/iac/stages", "--env", "dev", "--iac-root", "infra/iac"]
    assert cli.main(["changed-units", "--base", base, "--head", head, *common]) == 0
    assert capsys.readouterr().out == "\n"
    base, head = _commit(git, tmp_path, {"app.py": "c", "infra/iac/stages/dev/app/x.tf": "x"})
    assert cli.main(["changed-units", "--base", base, "--head", head, *common]) == 0
    assert capsys.readouterr().out == "app\n"
