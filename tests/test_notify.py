import json

import httpx
import pytest
import respx

from ayasee import cli, notify
from ayasee.changeset import build_changeset
from ayasee.notify import Event, build_card, post
from ayasee.planjson import load_plan
from ayasee.risk import classify, load_rules
from test_changeset import LEAK_SECRETS, RULES, _leaky_plan

URL = "https://example.invalid/hook/SECRET-TOKEN"
RUN = "https://github.com/o/r/actions/runs/1"


def _content(card):
    return card["attachments"][0]["content"]


def _facts(card):
    fs = next(b for b in _content(card)["body"] if b["type"] == "FactSet")
    return {f["title"]: f["value"] for f in fs["facts"]}


def test_prod_waiting_has_counts_and_link():
    card = build_card(
        Event.PROD_WAITING,
        repo="o/r",
        env="prod",
        sha="a" * 40,
        run_url=RUN,
        counts={"critical": 1, "high": 2, "medium": 3, "total": 6},
        approver="bob",
    )
    assert card["type"] == "message"
    att = card["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["contentUrl"] is None
    c = _content(card)
    assert (c["type"], c["version"]) == ("AdaptiveCard", "1.5")
    assert c["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert c["body"][0]["text"] == "Prod waiting for approval · o/r"
    assert _facts(card) == {
        "Repository": "o/r",
        "Environment": "prod",
        "Commit": "a" * 12,
        "Critical": "1",
        "High": "2",
        "Medium": "3",
        "Changes": "6",
        "Approver": "bob",
    }
    assert c["actions"] == [{"type": "Action.OpenUrl", "title": "Open run", "url": RUN}]


def test_no_counts_no_approver_facts():
    card = build_card(Event.DRIFT_CLEARED, repo="o/r", env="dev", sha="abc", run_url=RUN)
    assert set(_facts(card)) == {"Repository", "Environment", "Commit"}


def test_card_never_contains_values(tmp_path):
    d = _leaky_plan(tmp_path)
    items = []
    for plan in sorted(d.glob("*.json")):
        items += classify(plan.stem, load_plan(plan), load_rules(RULES))
    cs = build_changeset("abc", "prod", items)
    counts = {**cs["risk"], "total": len(cs["items"])}
    card = build_card(
        Event.PROD_WAITING, repo="o/r", env="prod", sha="abc", run_url=RUN, counts=counts
    )
    blob = json.dumps(card)
    for secret in LEAK_SECRETS:
        assert secret not in blob


@respx.mock
def test_post_success():
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    card = build_card(Event.PROD_DEPLOYED, repo="o/r", env="prod", sha="abc", run_url=RUN)
    assert post(URL, card) is True
    assert json.loads(route.calls[0].request.content) == card


@respx.mock
def test_post_failure_returns_false(capsys):
    respx.post(URL).mock(return_value=httpx.Response(500))
    assert post(URL, {"x": 1}) is False
    out = capsys.readouterr()
    assert "::warning::" in out.out
    assert "SECRET-TOKEN" not in out.out + out.err


@respx.mock
def test_post_network_error_returns_false(capsys):
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom " + URL))
    assert post(URL, {}) is False
    assert "SECRET-TOKEN" not in "".join(capsys.readouterr())


def test_post_refuses_non_https(capsys):
    assert post("http://example.invalid/SECRET-TOKEN", {}) is False
    out = capsys.readouterr().out
    assert "::warning::" in out and "SECRET-TOKEN" not in out
    assert post("not a url", {}) is False


def _cli(monkeypatch, *argv):
    monkeypatch.setattr(cli, "_COMMANDS", {"notify": notify._setup})
    return cli.main(
        ["notify", "--repo", "o/r", "--env", "prod", "--sha", "abc", "--run-url", RUN, *argv]
    )


def test_no_webhook_is_noop(monkeypatch, capsys):
    monkeypatch.delenv("AYASEE_TEAMS_WEBHOOK", raising=False)
    assert _cli(monkeypatch, "--event", "prod_waiting") == 0
    assert "AYASEE_TEAMS_WEBHOOK" in capsys.readouterr().out


@respx.mock
def test_cli_posts_with_changeset_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("AYASEE_TEAMS_WEBHOOK", URL)
    cs = tmp_path / "cs.json"
    cs.write_text(
        json.dumps({"risk": {"critical": 1, "high": 0, "medium": 2}, "items": [{}, {}, {}]})
    )
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    assert _cli(monkeypatch, "--event", "prod_waiting", "--changeset", str(cs)) == 0
    facts = _facts(json.loads(route.calls[0].request.content))
    assert facts["Critical"] == "1" and facts["Changes"] == "3"


@respx.mock
def test_cli_exits_zero_when_post_fails(monkeypatch):
    monkeypatch.setenv("AYASEE_TEAMS_WEBHOOK", URL)
    respx.post(URL).mock(return_value=httpx.Response(500))
    assert _cli(monkeypatch, "--event", "prod_failed") == 0


def test_cli_bad_arguments_exit_2(monkeypatch, tmp_path):
    monkeypatch.setenv("AYASEE_TEAMS_WEBHOOK", URL)
    with pytest.raises(SystemExit) as e:
        _cli(monkeypatch, "--event", "bogus")
    assert e.value.code == 2
    assert _cli(monkeypatch, "--event", "prod_waiting", "--changeset", str(tmp_path / "no")) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert _cli(monkeypatch, "--event", "prod_waiting", "--changeset", str(bad)) == 2


def test_detail_adds_a_fixed_status_line():
    card = build_card(
        Event.PROD_FAILED,
        repo="o/n",
        env="prod",
        sha="a" * 40,
        run_url="https://x",
        detail="replan_needed",
    )
    texts = [b.get("text") for b in card["attachments"][0]["content"]["body"]]
    assert "Plan refused: re-plan and approve again" in texts


def test_cli_detail_rejects_free_text():
    import pytest

    from ayasee import cli

    argv = ["notify", "--event", "prod_failed", "--repo", "o/n", "--env", "prod"]
    with pytest.raises(SystemExit):
        cli.main([*argv, "--sha", "a", "--run-url", "u", "--detail", "secret values"])


def test_breakglass_used_card_title():
    card = build_card(Event.BREAKGLASS_USED, repo="o/r", env="prod", sha="a" * 40, run_url=RUN)
    assert _content(card)["body"][0]["text"] == "Break-glass used · o/r"
    assert Event("breakglass_used") is Event.BREAKGLASS_USED
