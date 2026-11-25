"""environment_prefix (issue #31): no reusable workflow names a GitHub environment by a literal.

A job that said ``environment: prod`` would keep running in the adopter's own ``prod`` when a
prefix is set: without the reviewers of ``iac-prod`` and with the wrong secrets. These tests read
every reusable workflow and refuse that, then check that the names the workflows build are the
names ``ayasee config environments`` (and so the bootstrap and ``ayasee doctor``) expect.
"""

import re
from pathlib import Path

import pytest
import yaml

from ayasee import config
from ayasee.config import Config

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
REUSABLE = [
    "iac-staged.yml",
    "iac-pr.yml",
    "iac-pr-shared.yml",
    "iac-import.yml",
    "iac-nightly.yml",
    "iac-breakglass.yml",
]
# the only shapes of `environment:` a reusable workflow may have: a name built from outputs that the
# resolve step derived from the configuration (which carry environment_prefix)
ALLOWED = [
    r"\$\{\{ needs\.resolve\.outputs\.env-(stage-[1-4]|prod-plan|prod) \}\}",
    r"\$\{\{ needs\.resolve\.outputs\.pr-plan-environment \}\}",
    r"\$\{\{ needs\.resolve\.outputs\.environment-prefix \}\}drift-\$\{\{ matrix\.env \}\}",
    r"\$\{\{ needs\.validate\.outputs\.environment-prefix \}\}prod-breakglass",
    # break-glass of the workflow gate (D15): the request plans in prod-plan
    r"\$\{\{ needs\.validate\.outputs\.environment-prefix \}\}prod-plan",
    r"\$\{\{ needs\.shared-changes\.outputs\.environment-prefix \}\}prod-pr-plan",
    # iac-pr-shared: `ayasee pr-shared authorize` prints environment_prefix (of main) + prod-pr-plan
    r"\$\{\{ needs\.verify\.outputs\.environment \}\}",
    # iac-import: `ayasee import-target` prints environment_prefix + <first stage>-plan
    r"\$\{\{ needs\.resolve\.outputs\.environment \}\}",
]


def _jobs(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())["jobs"]


@pytest.mark.parametrize("name", REUSABLE)
def test_no_job_names_an_environment_by_a_literal(name):
    for job_name, job in _jobs(name).items():
        env = job.get("environment")
        if env is None:
            continue
        assert any(re.fullmatch(p, env) for p in ALLOWED), (name, job_name, env)


@pytest.mark.parametrize("name", REUSABLE)
def test_approvals_are_read_from_the_prefixed_environment(name):
    text = (WORKFLOWS / name).read_text()
    for m in re.finditer(r"ayasee approvals (list|rejected)[^\n]*", text):
        assert re.search(r'--env "\$[A-Z_]+_ENV"', m.group(0)), m.group(0)


@pytest.mark.parametrize("name", ["iac-staged.yml", "iac-breakglass.yml"])
def test_every_deployment_mirror_gets_the_prefix(name):
    steps = [
        s
        for job in _jobs(name).values()
        for s in job.get("steps", [])
        if "mirror-deployments" in (s.get("with") or {})
    ]
    assert steps
    for s in steps:
        assert s["with"]["environment-prefix"].endswith(".outputs.environment-prefix }}")


@pytest.mark.parametrize("prefix", ["", "iac-", "ayasee_"])
@pytest.mark.parametrize(
    "stages", [["dev", "uat", "prod"], ["test", "prod"], ["a", "b", "c", "prod"]]
)
def test_staged_environments_are_the_environments_the_bootstrap_creates(prefix, stages):
    cfg = Config(stages=stages, environment_prefix=prefix)
    layout = config.staged_layout(stages, "")
    out = config.staged_environments(layout, prefix)
    used = {v for k, v in out.items() if v}
    known = set(config.environment_names(cfg).values())
    assert used <= known
    assert used == {prefix + s for s in stages} | {prefix + "prod-plan"}
