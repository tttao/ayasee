"""Can this repository take a SARIF upload? (issue #55)

``iac-pr`` (its ``upload-sarif`` job) and ``iac-nightly`` (its ``rescan`` job) upload the scrubbed
Trivy and Checkov SARIF to GitHub code scanning, unless the caller passes ``code-scanning: false``.
Code scanning is free on a public repository; a private (or internal) one needs GitHub Code
Security (formerly part of GitHub Advanced Security), which GitHub sells on GitHub Team and GitHub
Enterprise only. Without it every upload fails ("Code scanning is not enabled for this
repository"), so ``upload-sarif`` is red on every pull request and the nightly rescan fails.

:func:`detect` answers from the API and returns a :class:`CodeScanning`. It fails closed: a
verdict is ``AVAILABLE`` only on evidence (a public repository, ``security_and_analysis`` showing
Code Security or Advanced Security enabled, or the code-scanning API answering), ``UNAVAILABLE``
only on GitHub's own word (``security_and_analysis`` showing it disabled, or the code-scanning API
saying it is not enabled), and ``UNKNOWN`` otherwise. Every call is a read: ``/repos/{repo}``
(Metadata read; ``security_and_analysis`` is shown to an administrator only) and one page of
``/repos/{repo}/code-scanning/alerts`` (Code scanning alerts read, the ``security_events`` scope);
a call that fails is evidence of nothing.

Callers decide what a verdict means: ``ayasee doctor`` warns when a caller uploads and the verdict
is not ``AVAILABLE``; ``ayasee init`` writes ``code-scanning: false`` into the callers for
``UNAVAILABLE`` (and for a private repository on Free, Pro or Team that it cannot read)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ayasee.github import GitHub, GitHubError
from ayasee.plansupport import _visibility

AVAILABLE, UNAVAILABLE, UNKNOWN = "available", "unavailable", "unknown"

# what GitHub answers on a repository without Code Security, in the forms seen so far
_NOT_ENABLED = re.compile(
    r"code scanning is not enabled|(advanced security|code security) must be enabled", re.I
)
_NO_ANALYSIS = re.compile(r"no analysis found", re.I)
_FEATURES = ("code_security", "advanced_security")


@dataclass(frozen=True)
class CodeScanning:
    """``verdict``: AVAILABLE, UNAVAILABLE or UNKNOWN. ``visibility``: public, private, internal or
    None when unread. ``evidence``: how the verdict was reached, short phrases, never a value."""

    verdict: str
    visibility: str | None
    evidence: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.verdict == AVAILABLE

    def how(self) -> str:
        return "; ".join(self.evidence) or "no evidence either way"


def _security_status(data: dict[str, Any]) -> str | None:
    """``enabled`` when Code Security or Advanced Security is enabled, ``disabled`` when every one
    of them that GitHub shows is disabled, None when none is shown (not an administrator)."""
    sa = data.get("security_and_analysis")
    if not isinstance(sa, dict):
        return None
    seen = []
    for key in _FEATURES:
        entry = sa.get(key)
        status = entry.get("status") if isinstance(entry, dict) else None
        if isinstance(status, str):
            seen.append(status.lower())
    if "enabled" in seen:
        return "enabled"
    if seen and all(s == "disabled" for s in seen):
        return "disabled"
    return None


def detect(gh: GitHub, repo: str) -> CodeScanning:
    """Whether ``repo`` takes a SARIF upload into code scanning."""
    try:
        data = gh.repository(repo)
    except GitHubError as exc:
        return CodeScanning(UNKNOWN, None, (f"cannot read the repository: {exc}",))
    vis = _visibility(data)
    if vis == "public":
        return CodeScanning(AVAILABLE, vis, ("a public repository: code scanning is free",))
    status = _security_status(data)
    if status == "enabled":
        return CodeScanning(AVAILABLE, vis, ("GitHub Code Security is enabled",))
    if status == "disabled":
        return CodeScanning(UNAVAILABLE, vis, ("GitHub Code Security is disabled",))
    try:
        gh.code_scanning_probe(repo)
    except GitHubError as exc:
        if exc.status == 403 and _NOT_ENABLED.search(exc.message):
            return CodeScanning(UNAVAILABLE, vis, ("the code-scanning API says it is not enabled",))
        if exc.status == 404 and _NO_ANALYSIS.search(exc.message):
            return CodeScanning(
                AVAILABLE, vis, ("code scanning is enabled and has no analysis yet",)
            )
        return CodeScanning(
            UNKNOWN, vis, (f"cannot read the code-scanning API ({exc.status or 'no answer'})",)
        )
    return CodeScanning(AVAILABLE, vis, ("the code-scanning API answers",))
