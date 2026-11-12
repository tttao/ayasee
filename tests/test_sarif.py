import hashlib
import json

from ayasee import cli
from ayasee.sarif import scrub

SECRET = "hunter2-super-secret"


def _s(label: str) -> str:
    return f"{label}={SECRET}"


def _loc() -> dict:
    return {
        "id": 0,
        "physicalLocation": {
            "artifactLocation": {
                "uri": "stages/dev/app/main.tf",
                "uriBaseId": "%SRCROOT%",
                "description": {"text": _s("artdesc")},
            },
            "region": {
                "startLine": 3,
                "startColumn": 1,
                "endLine": 3,
                "endColumn": 9,
                "snippet": {"text": _s("snippet")},
                "message": {"text": _s("regionmsg")},
                "sourceLanguage": _s("lang"),
            },
            "contextRegion": {"startLine": 1, "snippet": {"text": _s("ctx")}},
            "properties": {"v": _s("physprops")},
        },
        "message": {"text": _s("locmsg")},
        "annotations": [{"message": {"text": _s("annotation")}}],
        "properties": {"v": _s("locprops")},
    }


def _fixture() -> dict:
    logical = {"logicalLocations": [{"name": _s("logical"), "fullyQualifiedName": _s("fq")}]}
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "t",
                        "version": "1.0",
                        "notes": _s("drvnotes"),
                        "rules": [
                            {
                                "id": "R1",
                                "name": "Public",
                                "shortDescription": {"text": "Storage is public"},
                                "fullDescription": {"text": _s("fulldesc")},
                                "help": {"text": _s("helptext"), "markdown": _s("helpmd")},
                                "defaultConfiguration": {"level": "error", "rank": 5},
                                "properties": {"v": _s("ruleprops")},
                            },
                            {"id": "R2"},
                        ],
                    },
                    "extensions": [
                        {
                            "name": "ext",
                            "rules": [{"id": "R1", "shortDescription": {"text": "Ext rule"}}],
                        }
                    ],
                },
                "originalUriBaseIds": {"%SRCROOT%": {"uri": "file:///w/", "description": _s("d")}},
                "artifacts": [{"location": {"uri": "a"}, "contents": {"text": _s("contents")}}],
                "invocations": [
                    {"toolExecutionNotifications": [{"message": {"text": _s("notification")}}]}
                ],
                "properties": {"v": _s("runprops")},
                "results": [
                    {
                        "ruleId": "R1",
                        "ruleIndex": 0,
                        "level": "error",
                        "kind": "fail",
                        "message": {
                            "text": _s("message"),
                            "markdown": _s("markdown"),
                            "arguments": [SECRET],
                        },
                        "locations": [_loc(), logical],
                        "relatedLocations": [_loc()],
                        "codeFlows": [
                            {
                                "threadFlows": [
                                    {
                                        "locations": [
                                            {"location": _loc(), "state": {"x": {"text": SECRET}}}
                                        ]
                                    }
                                ]
                            }
                        ],
                        "stacks": [{"message": {"text": _s("stack")}}],
                        "attachments": [{"description": {"text": _s("attachment")}}],
                        "suppressions": [{"kind": "inSource", "justification": _s("justify")}],
                        "fingerprints": {"v": _s("fingerprint")},
                        "partialFingerprints": {"primaryLocationLineHash": "abc123"},
                        "fixes": [{"description": {"text": _s("fix")}}],
                        "properties": {"v": _s("resprops")},
                    },
                    {
                        "ruleId": "R1",
                        "rule": {"id": "R1", "index": 0, "toolComponent": {"name": "ext"}},
                        "message": {"text": _s("m2")},
                    },
                    {"ruleId": "R2", "message": {"text": _s("m3")}, "locations": [_loc()]},
                    {"ruleId": "R3", "message": {"text": SECRET}},
                    {"message": {"text": SECRET}},
                ],
            }
        ],
    }


def test_scrub_keeps_nothing_but_the_allow_list():
    out = scrub(_fixture())
    assert SECRET not in json.dumps(out)
    run = out["runs"][0]
    assert set(run) == {"tool", "originalUriBaseIds", "results"}
    assert run["originalUriBaseIds"] == {"%SRCROOT%": {"uri": "file:///w/"}}
    driver = run["tool"]["driver"]
    assert set(driver) == {"name", "version", "rules"}
    assert driver["rules"][0] == {
        "id": "R1",
        "name": "Public",
        "shortDescription": {"text": "Storage is public"},
        "defaultConfiguration": {"level": "error"},
    }
    r1, r2, r3, r4, r5 = run["results"]
    assert r1["message"] == {"text": "Storage is public"}
    assert set(r1) == {
        "ruleId",
        "ruleIndex",
        "level",
        "kind",
        "message",
        "locations",
        "partialFingerprints",
    }
    assert r1["partialFingerprints"] == {"primaryLocationLineHash": "abc123"}
    # logical-only location dropped; the physical one is reduced
    assert r1["locations"] == [
        {
            "physicalLocation": {
                "artifactLocation": {"uri": "stages/dev/app/main.tf", "uriBaseId": "%SRCROOT%"},
                "region": {"startLine": 3, "startColumn": 1, "endLine": 3, "endColumn": 9},
            }
        }
    ]
    assert r2["message"] == {"text": "Ext rule"}  # resolved through rule.toolComponent
    assert r3["message"] == {"text": "R2"}  # no short description: the rule id
    assert r4["message"] == {"text": "R3"}  # undeclared rule
    assert r5["message"] == {"text": "finding"}


def test_scrub_output_has_the_fields_github_requires():
    out = scrub({"runs": [{"tool": {"driver": {}}, "results": [{"message": {"text": SECRET}}]}]})
    assert out["version"] == "2.1.0" and out["$schema"].endswith("sarif-2.1.0.json")
    run = out["runs"][0]
    assert run["tool"]["driver"]["name"]
    assert all(isinstance(r["message"]["text"], str) for r in run["results"])
    assert SECRET not in json.dumps(out)


def test_scrub_survives_odd_shapes():
    odd = {"runs": [None, {"tool": 3, "results": [None, 5, {"locations": "x", "ruleIndex": True}]}]}
    out = scrub(odd)
    assert len(out["runs"]) == 2


def test_scrub_command(tmp_path):
    src, dst = tmp_path / "in.sarif", tmp_path / "out.sarif"
    src.write_text(json.dumps(_fixture()))
    assert cli.main(["sarif-scrub", "--in", str(src), "--out", str(dst)]) == 0
    assert SECRET not in dst.read_text()
    assert json.loads(dst.read_text())["version"] == "2.1.0"


def test_scrub_command_rejects_non_sarif(tmp_path, capsys):
    src = tmp_path / "in.sarif"
    src.write_text("not json")
    assert cli.main(["sarif-scrub", "--in", str(src), "--out", str(tmp_path / "o")]) == 1
    assert not (tmp_path / "o").exists()
    assert "sarif-scrub" in capsys.readouterr().err


def test_missing_fingerprints_are_derived_from_rule_file_and_line_only():
    doc = {
        "runs": [
            {
                "tool": {"driver": {"name": "t"}},
                "results": [
                    {
                        "ruleId": "R9",
                        "message": {"text": "x"},
                        "locations": [_loc()],
                    },
                    {"ruleId": "R9", "message": {"text": "y"}, "locations": [_loc()]},
                    {"ruleId": "R9", "message": {"text": "z"}},
                ],
            }
        ]
    }
    first, second, third = scrub(doc)["runs"][0]["results"]
    want = hashlib.sha256(b"R9stages/dev/app/main.tf3").hexdigest()[:32]
    assert first["partialFingerprints"] == {"primaryLocationLineHash": want}
    assert second["partialFingerprints"] == first["partialFingerprints"]  # stable
    assert "partialFingerprints" not in third  # no location, nothing to derive from
    assert SECRET not in json.dumps(first)


def test_uri_prefix_makes_paths_repository_relative():
    """Issue #4: scanners run on iac_root report paths relative to it; alerts need repo paths."""

    def doc(uri, base=None):
        art = {"uri": uri} | ({"uriBaseId": base} if base else {})
        return {
            "runs": [
                {
                    "tool": {"driver": {"rules": [{"id": "R1"}]}},
                    "results": [
                        {
                            "ruleId": "R1",
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": art,
                                        "region": {"startLine": 3},
                                    }
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    def uri(out):
        return out["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]

    assert (
        uri(scrub(doc("stages/dev/a.tf"), uri_prefix="infra/iac"))["uri"]
        == "infra/iac/stages/dev/a.tf"
    )
    assert (
        uri(scrub(doc("/stages/dev/a.tf"), uri_prefix="infra/iac/"))["uri"]
        == "infra/iac/stages/dev/a.tf"
    )
    kept = uri(scrub(doc("stages/a.tf", base="ROOTPATH"), uri_prefix="infra/iac"))
    assert kept == {"uri": "infra/iac/stages/a.tf", "uriBaseId": "ROOTPATH"}
    # absolute URIs and the default prefix are left alone
    assert uri(scrub(doc("file:///x/a.tf"), uri_prefix="infra/iac"))["uri"] == "file:///x/a.tf"
    assert uri(scrub(doc("stages/a.tf"), uri_prefix="."))["uri"] == "stages/a.tf"
    assert uri(scrub(doc("/stages/a.tf")))["uri"] == "/stages/a.tf"
    # the derived fingerprint follows the repository path
    a = scrub(doc("stages/a.tf"), uri_prefix="infra/iac")["runs"][0]["results"][0]
    b = scrub(doc("infra/iac/stages/a.tf"))["runs"][0]["results"][0]
    assert a["partialFingerprints"] == b["partialFingerprints"]


def test_scrub_command_takes_a_uri_prefix(tmp_path):
    src = tmp_path / "in.sarif"
    src.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "tool": {"driver": {}},
                        "results": [
                            {
                                "ruleId": "R",
                                "locations": [
                                    {"physicalLocation": {"artifactLocation": {"uri": "a.tf"}}}
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )
    out = tmp_path / "out.sarif"
    assert (
        cli.main(["sarif-scrub", "--in", str(src), "--out", str(out), "--uri-prefix", "infra/iac"])
        == 0
    )
    loc = json.loads(out.read_text())["runs"][0]["results"][0]["locations"][0]
    assert loc["physicalLocation"]["artifactLocation"]["uri"] == "infra/iac/a.tf"
