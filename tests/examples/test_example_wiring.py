"""The Databricks on Azure reference repository must match the workflows and docs it calls."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ayasee.config import load_config
from ayasee.ledger import open_store

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "examples" / "databricks-azure"
CALLERS = EX / ".github" / "workflows"
REUSABLE = ROOT / ".github" / "workflows"
TOOLS = ROOT / ".tools" / "bin"
FIXTURES = ROOT / "tests" / "fixtures" / "policy"

# caller file -> reusable workflow it calls
CALLS = {
    "pr.yml": "iac-pr.yml",
    "main.yml": "iac-staged.yml",
    "nightly.yml": "iac-nightly.yml",
    "breakglass.yml": "iac-breakglass.yml",
    "cockpit.yml": "cockpit-collect.yml",
}
PLACEHOLDER = "@0000000000000000000000000000000000000000 # replace with an ayasee release SHA"
USES = re.compile(r"^\s*uses:\s*(\S+)(.*)$", re.M)
RELEASED = re.compile(r"^@[0-9a-f]{40} # ayasee v\d+(\.\d+)*$")
UNITS = ("workspace", "unity-catalog", "grants")


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _on(wf: dict) -> dict:
    return wf.get("on", wf.get(True))  # PyYAML reads the key `on` as True


def _tool(name: str) -> str:
    local = TOOLS / name
    found = str(local) if local.exists() else shutil.which(name)
    if not found:
        pytest.skip(f"{name} is not installed")
    return found


def test_every_caller_exists_and_calls_the_expected_reusable_workflow():
    assert {p.name for p in CALLERS.glob("*.yml")} == set(CALLS)
    for caller, reusable in CALLS.items():
        jobs = _yaml(CALLERS / caller)["jobs"]
        assert len(jobs) == 1
        (job,) = jobs.values()
        assert re.fullmatch(rf"[^/\s]+/ayasee/\.github/workflows/{reusable}", _uses(caller)[0])
        assert "steps" not in job


def _uses(caller: str) -> tuple[str, str]:
    """(reference without the pin, the pin and trailing comment) of the single `uses:` line."""
    found = USES.findall((CALLERS / caller).read_text())
    assert len(found) == 1, caller
    ref, rest = found[0]
    path, _, pin = ref.partition("@")
    return path, f"@{pin}{rest}".rstrip()


def test_every_caller_pins_by_sha_or_the_placeholder():
    for caller in CALLS:
        _, pin = _uses(caller)
        assert pin == PLACEHOLDER.strip() or RELEASED.match(pin), (caller, pin)


def test_the_committed_callers_use_only_the_placeholder():
    for caller in CALLS:
        assert _uses(caller)[1] == PLACEHOLDER, caller


def test_no_caller_inherits_secrets():
    for caller in CALLS:
        text = (CALLERS / caller).read_text()
        assert "inherit" not in text, caller
        for job in _yaml(CALLERS / caller)["jobs"].values():
            assert isinstance(job.get("secrets", {}), dict), caller


def test_callers_pass_only_declared_secrets_and_inputs_and_all_required_ones():
    for caller, reusable in CALLS.items():
        call = _on(_yaml(REUSABLE / reusable))["workflow_call"]
        (job,) = _yaml(CALLERS / caller)["jobs"].values()
        declared_secrets = call.get("secrets", {})
        assert set(job.get("secrets", {})) <= set(declared_secrets), caller
        for name, spec in declared_secrets.items():
            if spec.get("required"):
                assert name in job["secrets"], (caller, name)
        declared_inputs = call.get("inputs", {})
        assert set(job.get("with", {})) <= set(declared_inputs), caller
        for name, spec in declared_inputs.items():
            if spec.get("required"):
                assert name in job["with"], (caller, name)


def test_the_stage_callers_pass_the_teams_webhook_and_nothing_else_secret():
    for caller in ("main.yml", "nightly.yml", "breakglass.yml"):
        (job,) = _yaml(CALLERS / caller)["jobs"].values()
        assert set(job["secrets"]) == {"teams-webhook"}, caller
    (job,) = _yaml(CALLERS / "pr.yml")["jobs"].values()
    assert "secrets" not in job


def test_caller_permissions_are_the_minimum_the_called_jobs_need():
    """Top level none; the job holds exactly the union of what the called jobs hold."""
    for caller, reusable in CALLS.items():
        wf = _yaml(CALLERS / caller)
        assert wf["permissions"] == {}, caller
        needed: dict[str, str] = {}
        for job in _yaml(REUSABLE / reusable)["jobs"].values():
            needed.update(job.get("permissions", {}))
        (job,) = wf["jobs"].values()
        assert job["permissions"] == needed, caller


def test_caller_permissions_match_the_documented_scopes():
    docs = {
        "pr.yml": (
            "docs/concepts/pr-checks.md",
            ["contents: read", "id-token: write", "pull-requests: write", "security-events: write"],
        ),
        "nightly.yml": (
            "docs/concepts/drift.md",
            [
                "id-token: write",
                "contents: read",
                "issues: write",
                "actions: read",
                "security-events: write",
            ],
        ),
        "cockpit.yml": (
            "docs/cockpit/setup.md",
            ["contents: read", "pages: write", "id-token: write"],
        ),
    }
    for caller, (doc, scopes) in docs.items():
        text = (ROOT / doc).read_text()
        (job,) = _yaml(CALLERS / caller)["jobs"].values()
        have = {f"{k}: {v}" for k, v in job["permissions"].items()}
        for scope in scopes:
            assert scope in have, (caller, scope)
            assert scope in text.replace("`", ""), (doc, scope)


def test_triggers():
    assert set(_on(_yaml(CALLERS / "pr.yml"))) == {"pull_request"}
    assert _on(_yaml(CALLERS / "main.yml")) == {"push": {"branches": ["main"]}}
    nightly = _on(_yaml(CALLERS / "nightly.yml"))
    assert len(nightly["schedule"]) == 1 and nightly["schedule"][0]["cron"]
    bg = _on(_yaml(CALLERS / "breakglass.yml"))
    assert set(bg) == {"workflow_dispatch"}
    # R71: no units input, break-glass always plans the whole prod tree
    assert set(bg["workflow_dispatch"]["inputs"]) == {"incident", "sha", "allow-rollback"}
    for caller in CALLS:
        assert "pull_request_target" not in _on(_yaml(CALLERS / caller)), caller


def _called_environments() -> set[str]:
    """Every `environment:` of the reusable workflows the callers use (not cockpit-collect)."""
    stages = load_config(EX / ".ayasee.yaml").stages
    found: set[str] = set()
    for reusable in set(CALLS.values()) - {"cockpit-collect.yml"}:
        for job in _yaml(REUSABLE / reusable)["jobs"].values():
            env = job.get("environment")
            if isinstance(env, dict):
                env = env["name"]
            if env is None:
                continue
            if "${{" in env:
                assert env == "drift-${{ matrix.env }}", env
                found |= {f"drift-{s}" for s in stages}
            else:
                found.add(env)
    return found


def test_bootstrap_creates_every_environment_the_workflows_use():
    text = (EX / "bootstrap" / "main.tf").read_text()
    stages = load_config(EX / ".ayasee.yaml").stages
    assert re.search(r"stages\s*=\s*\[" + ",\\s*".join(f'"{s}"' for s in stages) + r"\]", text)
    # the module creates the stages, drift-<stage>, dev-plan, prod-plan, prod, prod-breakglass
    module = (ROOT / "modules" / "github-environments" / "main.tf").read_text()
    for fixed in ("dev-plan", "prod-plan", "prod-breakglass", '"drift-${s}"'):
        assert fixed in module
    created = (
        set(stages) | {f"drift-{s}" for s in stages} | {"dev-plan", "prod-plan", "prod-breakglass"}
    )
    assert _called_environments() == created
    # identities: one per environment, listed in the bootstrap locals
    assert 'concat(\n    ["dev-plan"],\n    local.stages,' in text
    assert '["prod-plan", "prod-breakglass"]' in text
    assert '"drift-${s}"' in text
    assert "for_each = toset(local.environments)" in text


def test_bootstrap_writes_the_variables_the_workflows_read():
    text = (EX / "bootstrap" / "main.tf").read_text()
    read = set()
    for reusable in set(CALLS.values()) - {"cockpit-collect.yml"}:
        read |= set(re.findall(r"vars\.([A-Z_]+)", (REUSABLE / reusable).read_text()))
    assert read == {
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "AYASEE_LEDGER_URL",
        "AYASEE_KEY_VAULT_KEY_ID",
    }
    for name in read:
        assert name in text, name
    assert "github_actions_environment_secret" not in text  # R30: variables, not secrets
    assert "../../../modules/azure-stage-identity" in text
    assert "../../../modules/github-environments" in text


def test_ayasee_yaml_loads_and_declares_the_example_choices():
    cfg = load_config(EX / ".ayasee.yaml")
    assert cfg.stages == ["dev", "uat", "prod"]
    assert cfg.engine == "opentofu"
    assert cfg.units_root == "stages"
    assert cfg.allow_cascade is False
    assert cfg.solo_mode is False
    assert cfg.ledger.url.startswith("azure-blob://")
    # the store URL must also parse: the account host and one container, prefix as a query
    assert type(open_store(cfg.ledger.url)).__name__ == "AzureBlobStore"


def test_root_hcl_configures_the_azurerm_backend_with_oidc_and_entra_auth():
    text = (EX / "root.hcl").read_text()
    assert 'backend = "azurerm"' in text
    assert re.search(r"use_oidc\s*=\s*true", text)
    assert re.search(r"use_azuread_auth\s*=\s*true", text)
    assert "key" in text and "local.stage" in text and "local.unit" in text
    assert not re.search(r"(access_key|sas_token|client_secret)", text)


def test_every_stage_has_every_unit_with_a_lock_file():
    for stage in ("dev", "uat", "prod"):
        for unit in UNITS:
            d = EX / "stages" / stage / unit
            assert (d / "terragrunt.hcl").is_file(), d
            lock = d / ".terraform.lock.hcl"
            assert lock.is_file(), f"{lock} is missing: see the README on generating lock files"
            text = lock.read_text()
            source = "hashicorp/azurerm" if unit == "workspace" else "databricks/databricks"
            assert f"registry.opentofu.org/{source}" in text
            assert text.count('"h1:') >= 2, d  # linux_amd64 and darwin_arm64 at least
    on_disk = {p.parent for p in (EX / "stages").rglob("terragrunt.hcl")}
    assert len(on_disk) == 9


def test_units_depend_on_each_other_and_use_the_shared_code():
    for stage in ("dev", "uat", "prod"):
        base = EX / "stages" / stage
        assert "dependency" not in (base / "workspace" / "terragrunt.hcl").read_text()
        uc = (base / "unity-catalog" / "terragrunt.hcl").read_text()
        assert 'config_path = "../workspace"' in uc
        assert "dependency.workspace.outputs.workspace_id" in uc
        assert "dependency.workspace.outputs.workspace_url" in uc
        assert re.search(r'isolation_mode\s*=\s*"ISOLATED"', uc)
        grants = (base / "grants" / "terragrunt.hcl").read_text()
        assert 'config_path = "../unity-catalog"' in grants
        assert "principals" in grants
        for unit in UNITS:
            assert f"/shared/{unit}" in (base / unit / "terragrunt.hcl").read_text()


def test_prod_workspace_has_no_public_network_access():
    text = (EX / "stages" / "prod" / "workspace" / "terragrunt.hcl").read_text()
    assert re.search(r"public_network_access_enabled\s*=\s*false", text)


def test_no_secret_values_in_the_hcl():
    for path in list(EX.rglob("*.hcl")) + list(EX.rglob("*.tf")):
        if ".terraform" in path.parts or path.name == ".terraform.lock.hcl":
            continue
        assert not re.search(
            r"(?i)(client_secret|access_key|sas_token|password|token\s*=)", path.read_text()
        ), path


def test_renovate_requires_manual_review_of_databricks_provider_updates():
    cfg = json.loads((EX / "renovate.json").read_text())
    assert "helpers:pinGitHubActionDigests" in cfg["extends"]
    rule = next(r for r in cfg["packageRules"] if "databricks/databricks" in r["matchPackageNames"])
    assert rule["automerge"] is False
    assert "breaking-check" in rule["labels"]
    assert "Breaking" in rule["description"]


def test_ownership_contract_names_both_owners_and_the_handover():
    text = (EX / "OWNERSHIP.md").read_text().lower()
    for term in (
        "metastore assignment",
        "catalogs",
        "storage credentials",
        "external locations",
        "grants",
        "Declarative Automation Bundles",
        "jobs",
        "pipelines",
        "notebooks",
        "models",
        "handing an object over",
        "account-level",
    ):
        assert term.lower() in text, term


def test_readme_covers_the_adoption_steps_and_the_cascade_rule():
    text = (EX / "README.md").read_text()
    for term in (
        "bootstrap",
        "role assignment",
        "ledger",
        "Key Vault",
        "release SHA",
        "first pull request",
        "first merge",
        "cockpit",
        "allow_cascade",
        "R52",
        "tofu providers lock",
    ):
        assert term.lower() in text.lower(), term
    for workflow in ("iac-pr", "iac-staged", "iac-nightly", "iac-breakglass"):
        assert workflow in text


# ---- tools: skipped when missing ---------------------------------------------------------


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300)


def test_terragrunt_formatting_and_validation_of_the_stages():
    tg = _tool("terragrunt")
    fmt = _run([tg, "hcl", "format", "--check", "--non-interactive"], EX)
    assert fmt.returncode == 0, fmt.stdout + fmt.stderr
    val = _run([tg, "hcl", "validate", "--non-interactive"], EX)
    assert val.returncode == 0, val.stdout + val.stderr


@pytest.mark.integration
def test_the_shared_code_and_bootstrap_validate(tmp_path):
    tofu = shutil.which("tofu")
    if not tofu:
        pytest.skip("tofu is not installed")
    for src in [EX / "bootstrap", *[EX / "shared" / u for u in UNITS]]:
        if src.name == "bootstrap":
            work = src  # its module sources are relative to it; .terraform is git-ignored
        else:
            work = tmp_path / src.name
            shutil.copytree(src, work, ignore=shutil.ignore_patterns(".terraform"))
            shutil.copy(EX / "stages" / "dev" / src.name / ".terraform.lock.hcl", work)
        init = _run([tofu, "init", "-backend=false", "-input=false", "-lockfile=readonly"], work)
        assert init.returncode == 0, (src, init.stdout + init.stderr)
        val = _run([tofu, "validate"], work)
        assert val.returncode == 0, (src, val.stdout + val.stderr)
    fmt = _run([tofu, "fmt", "-check", "-recursive"], EX / "bootstrap")
    assert fmt.returncode == 0, fmt.stdout


def _conftest(plan: str) -> subprocess.CompletedProcess:
    conftest = _tool("conftest")
    return _run(
        [
            conftest,
            "test",
            "--no-color",
            "--policy",
            str(ROOT / "policy" / "databricks"),
            "--namespace",
            "ayasee.databricks",
            "--data",
            str(FIXTURES / "prod.data.json"),
            str(FIXTURES / plan),
        ],
        ROOT,
    )


def test_the_policy_pack_passes_the_example_prod_plan():
    result = _conftest("example_prod_plan.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failures" in result.stdout and "0 warnings" in result.stdout


def test_the_policy_pack_rejects_the_example_with_public_access():
    result = _conftest("example_prod_plan_public.json")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SG-DBX-003" in result.stdout


def test_the_canned_plan_matches_the_example_prod_resources():
    plan = json.loads((FIXTURES / "example_prod_plan.json").read_text())
    by_type = {rc["type"]: rc["change"]["after"] for rc in plan["resource_changes"]}
    assert by_type["azurerm_databricks_workspace"]["public_network_access_enabled"] is False
    assert by_type["databricks_catalog"]["isolation_mode"] == "ISOLATED"
    for rtype in by_type:
        assert re.search(
            rf'resource "{rtype}"',
            "".join(p.read_text() for p in (EX / "shared").glob("*/main.tf")),
        ), rtype


def test_readme_documents_incremental_first_adoption():
    text = (EX / "README.md").read_text()
    section = text[text.index("First-time adoption") : text.index("### 8.")]
    for term in (
        "Merge 1",
        "Merge 2",
        "Merge 3",
        "`workspace`",
        "`unity-catalog`",
        "`grants`",
        "allow_cascade",
        "mock",
    ):
        assert term in section, term
    assert section.index("Merge 1") < section.index("Merge 2") < section.index("Merge 3")
    assert re.search(r"allow_cascade: true` is not the way to bootstrap", section)
    assert "allow_cascade: true is not the way to bootstrap" in (EX / ".ayasee.yaml").read_text()


def test_azurerm_constraint_is_the_same_in_bootstrap_and_the_workspace_unit():
    found = {
        p.relative_to(EX).as_posix(): re.search(
            r'hashicorp/azurerm"\s*version\s*=\s*"([^"]+)"', p.read_text()
        ).group(1)
        for p in (EX / "bootstrap" / "main.tf", EX / "shared" / "workspace" / "main.tf")
    }
    assert len(set(found.values())) == 1, found
    lock = (EX / "stages" / "dev" / "workspace" / ".terraform.lock.hcl").read_text()
    assert f'constraints = "{next(iter(found.values()))}"' in lock


def test_cockpit_workflow_run_is_limited_to_main():
    on = yaml.safe_load((EX / ".github" / "workflows" / "cockpit.yml").read_text())[True]
    assert on["workflow_run"]["branches"] == ["main"]
