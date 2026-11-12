"""Sanitized change set and gate summary.

The serializer is an allow-list: only unit, kind, address, actions, tier, rule ids and
principal deltas are ever emitted. Plan values (before/after/unknown) are never read.

Root-module output changes (R72) become items of kind ``output`` with address
``output.<name>`` and tier ``none``: they count in the total (so an output-only change still
goes through the prod gate) but not in the per-action resource counts. Their values, sensitive
or not, are never read.
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import resources
from pathlib import Path

from ayasee import cli
from ayasee.planjson import (
    Action,
    OutputChange,
    PlanFormatError,
    load_output_changes,
    load_plan,
)
from ayasee.risk import PrincipalDelta, RiskItem, RuleError, Tier, classify, load_rules

SCHEMA = "ayasee.changeset/v1"
_COUNTED = ("create", "update", "delete", "replace", "import", "move", "forget")
_IDLE = {Action.NOOP, Action.READ}
_RISK_TIERS = (Tier.CRITICAL, Tier.HIGH, Tier.MEDIUM)


def _tier_name(tier: Tier) -> str:
    return tier.name.lower()


def _principals(delta: PrincipalDelta | None) -> dict | None:
    if delta is None:
        return None
    return {
        "gained": {p: list(v) for p, v in delta.gained.items()},
        "lost": {p: list(v) for p, v in delta.lost.items()},
    }


def build_changeset(
    sha: str,
    env: str,
    items: list[RiskItem],
    outputs: list[tuple[str, OutputChange]] | None = None,
    shared_units: list[str] | None = None,
) -> dict:
    """Build the allow-listed change set; items with only no-op/read actions are dropped.

    ``outputs`` are ``(unit, output change)`` pairs; their non-idle ones become ``output`` items.
    ``shared_units`` (units that serve every stage) are listed when the change set touches them,
    because the stage name alone does not tell a reviewer the blast radius.
    """
    real = [i for i in items if set(i.actions) - _IDLE]
    real.sort(key=lambda i: (-int(i.tier), i.unit, i.address))
    counts = dict.fromkeys(_COUNTED, 0)
    for item in real:
        for action in set(item.actions):
            if action.value in counts:
                counts[action.value] += 1
    rows = [
        {
            "unit": i.unit,
            "kind": "resource",
            "address": i.address,
            "actions": [a.value for a in i.actions],
            "tier": _tier_name(i.tier),
            "rules": list(i.rule_ids),
            "principals": _principals(i.principal_delta),
        }
        for i in real
    ]
    rows += [
        {
            "unit": unit,
            "kind": "output",
            "address": f"output.{oc.name}",
            "actions": [a.value for a in oc.actions],
            "tier": _tier_name(Tier.NONE),
            "rules": [],
            "principals": None,
        }
        for unit, oc in sorted(outputs or [], key=lambda p: (p[0], p[1].name))
        if set(oc.actions) - _IDLE
    ]
    shared = set(shared_units or [])
    return {
        "schema": SCHEMA,
        "sha": sha,
        "env": env,
        "counts": counts,
        "risk": {_tier_name(t): sum(1 for i in real if i.tier is t) for t in _RISK_TIERS},
        "shared_units": sorted({r["unit"] for r in rows} & shared),
        "items": rows,
    }


def _md(value: object) -> str:
    """Inline-code-safe text: PR code chooses addresses and principal names, so no backticks,
    no line breaks and no HTML can get out of the code span."""
    text = " ".join(str(value).split())
    return text.replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")


def _line(item: dict, shared: frozenset[str] = frozenset()) -> str:
    text = f"- `{_md(item['unit'])}` `{_md(item['address'])}` ({', '.join(item['actions'])})"
    if item["unit"] in shared:
        text += " **shared**"
    if item["rules"]:
        text += f" [{', '.join(item['rules'])}]"
    principals = item["principals"]
    if principals:
        for side in ("gained", "lost"):
            for who, privs in principals[side].items():
                text += f"\n  - {side}: `{_md(who)}` {_md(', '.join(privs))}"
    return text


def render_summary(cs: dict) -> str:
    risk = cs["risk"]
    items = cs["items"]
    shared = frozenset(cs.get("shared_units") or [])
    out = [
        f"### Prod gate: {risk['critical']} critical · {risk['high']} high · "
        f"{risk['medium']} medium · {len(items)} changes",
        "",
    ]
    if shared:
        names = ", ".join(f"`{_md(u)}`" for u in sorted(shared))
        out += [
            f"> **Shared by every stage:** {names}. These units exist once and carry the traffic "
            "of every stage, so a change to them reaches all stages, not only prod.",
            "",
        ]
    for tier in _RISK_TIERS:
        name = _tier_name(tier)
        group = [i for i in items if i["tier"] == name]
        if group:
            out += [f"#### {name.capitalize()}", "", *(_line(i, shared) for i in group), ""]
    rest = [i for i in items if i["tier"] == "none"]
    if rest:
        out += [
            "<details>",
            f"<summary>{len(rest)} other changes</summary>",
            "",
            *(_line(i, shared) for i in rest),
            "",
            "</details>",
            "",
        ]
    return "\n".join(out)


# The packaged rule files, loaded in this order before any adopter file (issue #1: a second,
# provider-agnostic pack next to the Databricks one).
PACKAGED_RULES = ("databricks-azure.yaml", "cloudflare.yaml")


def _default_rules() -> list[Path]:
    packaged = Path(str(resources.files("ayasee").joinpath("rules")))
    if not (packaged / PACKAGED_RULES[0]).exists():
        # source checkout: the repo-root files are the single source (force-included in the wheel)
        packaged = Path(__file__).resolve().parents[2] / "rules"
    return [packaged / name for name in PACKAGED_RULES]


def _run(args: argparse.Namespace) -> int:
    plan_dir = Path(args.plan_json)
    plans = sorted(plan_dir.glob("*.json")) if plan_dir.is_dir() else []
    if not plans:
        print(f"ayasee: no <unit>.json plans found in {plan_dir}", file=sys.stderr)
        return 1
    try:
        rules = load_rules(*_default_rules(), *(Path(p) for p in args.rules or []))
        items: list[RiskItem] = []
        outputs: list[tuple[str, OutputChange]] = []
        for plan in plans:
            items += classify(plan.stem, load_plan(plan), rules)
            outputs += [(plan.stem, oc) for oc in load_output_changes(plan)]
        shared = [u for u in (args.shared_units or "").split(",") if u]
        cs = build_changeset(args.sha, args.env, items, outputs, shared)
        Path(args.out).write_text(json.dumps(cs, indent=2) + "\n")
        if args.summary:
            with open(args.summary, "a") as fh:
                fh.write(render_summary(cs))
    except (PlanFormatError, RuleError, OSError) as exc:
        print(f"ayasee: changeset failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan-json", required=True, help="directory of <unit>.json plans")
    parser.add_argument("--sha", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary", help="file to append the Markdown summary to")
    parser.add_argument("--rules", action="append", help="extra rules file (repeatable)")
    parser.add_argument(
        "--shared-units",
        default="",
        help="comma list of units that serve every stage (shared_units); flagged in the summary",
    )
    parser.set_defaults(handler=_run)


cli.register("changeset", _setup)
