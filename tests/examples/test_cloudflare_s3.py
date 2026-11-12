"""The Cloudflare and S3 reference repository (issue #1) must match the workflows it calls."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ayasee import changeset, cli, config  # noqa: F401  (changeset registers its subcommand)
from ayasee.config import load_config, shared_layout_problems, staged_layout, unsupported

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "examples" / "cloudflare-s3"
CALLERS = EX / ".github" / "workflows"
REUSABLE = ROOT / ".github" / "workflows"
TOOLS = ROOT / ".tools" / "bin"
PLACEHOLDER = "@0000000000000000000000000000000000000000 # replace with an ayasee release SHA"
CALLS = {
    "pr.yml": "iac-pr.yml",
    "main.yml": "iac-staged.yml",
    "nightly.yml": "iac-nightly.yml",
    "breakglass.yml": "iac-breakglass.yml",
}
TOKENS = ("CLOUDFLARE_API_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
UNITS = {"test": {"dns"}, "prod": {"dns", "tunnel", "access"}}


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _tool(name: str) -> str:
    local = TOOLS / name
    found = str(local) if local.exists() else shutil.which(name)
    if not found:
        pytest.skip(f"{name} is not installed")
    return found


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def test_configuration_declares_the_profile(capsys):
    cfg = load_config(EX / ".ayasee.yaml")
    assert cfg.stages == ["test", "prod"]
    assert cfg.identity == "secrets" and cfg.plan_encryption.key_provider == "pbkdf2"
    assert cfg.shared_units == ["tunnel", "access"]
    assert cfg.ledger.url is None  # set per environment (AYASEE_LEDGER_URL), see the README
    assert unsupported(cfg) == []
    layout = staged_layout(cfg.stages, "")
    assert (layout["run-dev"], layout["pre-prod-stage"]) == ("false", "test")
    assert "DEVIATION: identity: secrets" in capsys.readouterr().err
    assert config.label_keys(cfg) == [{"kind": "deviation", "setting": "identity"}]


def test_shared_units_exist_in_prod_only():
    cfg = load_config(EX / ".ayasee.yaml")
    assert shared_layout_problems(cfg, EX) == []
    for stage, units in UNITS.items():
        found = {p.parent.name for p in (EX / "stages" / stage).glob("*/terragrunt.hcl")}
        assert found == units, stage


def test_every_unit_has_a_lock_file_and_existing_shared_code():
    for stage, units in UNITS.items():
        for unit in units:
            d = EX / "stages" / stage / unit
            assert (d / ".terraform.lock.hcl").is_file(), d
            assert "cloudflare/cloudflare" in (d / ".terraform.lock.hcl").read_text()
            source = re.search(r'/shared/([a-z-]+)"', (d / "terragrunt.hcl").read_text())
            assert source and (EX / "shared" / source.group(1) / "main.tf").is_file(), d


def test_no_credential_in_the_hcl():
    text = "".join(p.read_text() for p in EX.rglob("*") if p.suffix in {".hcl", ".tf"})
    assert not re.search(r"api_token\s*=|AKIA[0-9A-Z]{16}|secret_key\s*=|access_key\s*=", text)


def test_every_caller_calls_its_reusable_workflow_at_the_placeholder():
    assert {p.name for p in CALLERS.glob("*.yml")} == set(CALLS)
    for caller, reusable in CALLS.items():
        wf = _yaml(CALLERS / caller)
        (job,) = wf["jobs"].values()
        assert (
            job["uses"] == f"OWNER/ayasee/.github/workflows/{reusable}" + PLACEHOLDER.split(" #")[0]
        )
        assert PLACEHOLDER in (CALLERS / caller).read_text()
        assert "inherit" not in (CALLERS / caller).read_text()


def test_callers_pass_the_provider_tokens_by_name_and_only_declared_secrets():
    for caller, reusable in CALLS.items():
        (job,) = _yaml(CALLERS / caller)["jobs"].values()
        declared = _yaml(REUSABLE / reusable)[True]["workflow_call"].get("secrets", {})
        passed = job.get("secrets", {})
        assert set(passed) <= set(declared), caller
        for token in TOKENS:
            assert passed[token] == f"${{{{ secrets.{token} }}}}", (caller, token)
        # the plan passphrase goes only where a prod plan is made or applied
        has_pass = "AYASEE_PLAN_PASSPHRASE" in passed
        assert has_pass == (reusable in {"iac-staged.yml", "iac-breakglass.yml"}), caller
        if "ledger-token" in declared:
            assert passed["ledger-token"] == "${{ secrets.AYASEE_LEDGER_TOKEN }}", caller


def test_bootstrap_creates_the_environments_and_the_ledger_variable():
    text = (EX / "bootstrap" / "main.tf").read_text()
    assert 'stages               = ["test", "prod"]' in text
    assert "shared_units_pr_plan = true" in text
    assert "git-branch://${local.workspace}?branch=ayasee-ledger&remote=origin" in text
    envs = re.search(r"ledger_envs = \[(.*?)\]", text).group(1)
    assert {e.strip().strip('"') for e in envs.split(",")} == {
        "test",
        "prod-plan",
        "prod",
        "prod-breakglass",
        "drift-test",
        "drift-prod",
    }
    assert "azure" not in text.lower()


def test_readme_covers_the_steps_the_issue_asked_for():
    text = (EX / "README.md").read_text()
    for needle in (
        "AYASEE_LEDGER_URL",
        "git-branch:///home/runner/work/<repo>/<repo>?branch=ayasee-ledger&remote=origin",
        "Contents: read and write",
        "github-branch://my-org/my-estate?branch=ayasee-ledger",
        "never with `github.token`",
        "prod-pr-plan",
        "AYASEE_PLAN_PASSPHRASE",
    ):
        assert needle in text, needle


def test_a_tunnel_change_is_high_risk_and_flagged_shared(tmp_path):
    """The rules of the Cloudflare pack and shared_units, end to end through `ayasee changeset`."""
    plan = {
        "format_version": "1.2",
        "resource_changes": [
            {
                "address": "cloudflare_zero_trust_tunnel_cloudflared_config.this",
                "mode": "managed",
                "type": "cloudflare_zero_trust_tunnel_cloudflared_config",
                "name": "this",
                "provider_name": "registry.opentofu.org/cloudflare/cloudflare",
                "change": {
                    "actions": ["update"],
                    "before": {"config": {"ingress": [{"hostname": "a.example.com"}]}},
                    "after": {"config": {"ingress": [{"hostname": "b.example.com"}]}},
                    "after_unknown": {},
                    "before_sensitive": {},
                    "after_sensitive": {},
                },
            }
        ],
    }
    plans = tmp_path / "json"
    plans.mkdir()
    (plans / "tunnel.json").write_text(json.dumps(plan))
    out, summary = tmp_path / "cs.json", tmp_path / "summary.md"
    args = ["changeset", "--plan-json", str(plans), "--sha", "s", "--env", "prod"]
    args += ["--out", str(out), "--summary", str(summary), "--shared-units", "tunnel,access"]
    assert cli.main(args) == 0
    cs = json.loads(out.read_text())
    assert cs["shared_units"] == ["tunnel"]
    (item,) = cs["items"]
    assert item["tier"] == "high" and "cf-tunnel-ingress" in item["rules"]
    assert "**Shared by every stage:** `tunnel`" in summary.read_text()


def test_terragrunt_formatting_and_validation_of_the_stages():
    tg = _tool("terragrunt")
    fmt = _run([tg, "hcl", "format", "--check", "--non-interactive"], EX)
    assert fmt.returncode == 0, fmt.stdout + fmt.stderr
    val = _run([tg, "hcl", "validate", "--non-interactive"], EX)
    assert val.returncode == 0, val.stdout + val.stderr


@pytest.mark.integration
def test_the_shared_code_and_bootstrap_validate(tmp_path):
    tofu = _tool("tofu")
    for src in [EX / "bootstrap", *(EX / "shared").iterdir()]:
        if src.name == "bootstrap":
            work = src  # its module source is relative to it; .terraform is git-ignored
        else:
            work = tmp_path / src.name
            shutil.copytree(src, work)
            shutil.copy(EX / "stages" / "prod" / "dns" / ".terraform.lock.hcl", work)
        init = _run([tofu, "init", "-backend=false", "-input=false", "-lockfile=readonly"], work)
        assert init.returncode == 0, (src, init.stdout + init.stderr)
        val = _run([tofu, "validate"], work)
        assert val.returncode == 0, (src, val.stdout + val.stderr)
    fmt = _run([tofu, "fmt", "-check", "-recursive"], EX)
    assert fmt.returncode == 0, fmt.stdout
