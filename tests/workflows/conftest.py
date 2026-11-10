"""Helpers shared by the workflow tests (import them with ``from conftest import ...``)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SHA_REF = re.compile(r"@[0-9a-f]{40}$")
AYASEE_USES = re.compile(r"^[^/]+/ayasee/actions/[a-z]+@([0-9a-f]{40})$")


def steps(wf, job):
    return wf["jobs"][job]["steps"]


def index(job_steps, pred):
    return next(i for i, s in enumerate(job_steps) if pred(s))
