import json

import httpx
import respx

from ayasee import cli
from ayasee.prcomment import MARKER

BASE = "https://api.github.com"


def _body(tmp_path, text="### Prod gate: 0 critical\n"):
    f = tmp_path / "body.md"
    f.write_text(text)
    return str(f)


def _run(tmp_path, monkeypatch, text="### Prod gate: 0 critical\n"):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    return cli.main(
        ["pr-comment", "--repo", "o/n", "--pr", "7", "--body-file", _body(tmp_path, text)]
    )


@respx.mock
def test_creates_a_comment_with_the_marker(tmp_path, monkeypatch):
    respx.get(f"{BASE}/repos/o/n/issues/7/comments").mock(return_value=httpx.Response(200, json=[]))
    post = respx.post(f"{BASE}/repos/o/n/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 5})
    )
    assert _run(tmp_path, monkeypatch) == 0
    body = json.loads(post.calls[0].request.content)["body"]
    assert body.startswith(MARKER) and "### Prod gate" in body
    assert MARKER == "<!-- ayasee:pr-plan -->"


@respx.mock
def test_updates_the_existing_bot_comment_across_pages(tmp_path, monkeypatch):
    bot = {
        "id": 9,
        "body": f"{MARKER}\nold",
        "user": {"type": "Bot", "login": "github-actions[bot]"},
    }
    other = {"id": 3, "body": "hello", "user": {"type": "User"}}
    spoof = {"id": 4, "body": f"{MARKER}\nfake", "user": {"type": "User"}}
    respx.get(f"{BASE}/repos/o/n/issues/7/comments", params={"per_page": "100"}).mock(
        return_value=httpx.Response(
            200,
            json=[other, spoof],
            headers={"Link": f'<{BASE}/repos/o/n/issues/7/comments?page=2>; rel="next"'},
        )
    )
    respx.get(f"{BASE}/repos/o/n/issues/7/comments", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=[bot])
    )
    patch = respx.patch(f"{BASE}/repos/o/n/issues/comments/9").mock(
        return_value=httpx.Response(200, json={})
    )
    assert _run(tmp_path, monkeypatch, "new text") == 0
    assert json.loads(patch.calls[0].request.content) == {"body": f"{MARKER}\nnew text"}


@respx.mock
def test_oversized_body_is_truncated(tmp_path, monkeypatch):
    respx.get(f"{BASE}/repos/o/n/issues/7/comments").mock(return_value=httpx.Response(200, json=[]))
    post = respx.post(f"{BASE}/repos/o/n/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 5})
    )
    assert _run(tmp_path, monkeypatch, "x" * 100_000) == 0
    assert len(json.loads(post.calls[0].request.content)["body"]) <= 65536


@respx.mock
def test_github_error_exits_nonzero_without_the_token(tmp_path, monkeypatch, capsys):
    respx.get(f"{BASE}/repos/o/n/issues/7/comments").mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible"})
    )
    assert _run(tmp_path, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "403" in err and "tok" not in err.replace("token", "")


def test_missing_body_file_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    args = ["pr-comment", "--repo", "o/n", "--pr", "7", "--body-file", str(tmp_path / "nope")]
    assert cli.main(args) == 1


@respx.mock
def test_list_issue_comments_refuses_foreign_next_host():
    from ayasee.github import GitHub, GitHubError

    respx.get(f"{BASE}/repos/o/n/issues/7/comments").mock(
        return_value=httpx.Response(
            200, json=[], headers={"Link": '<https://evil.example/x>; rel="next"'}
        )
    )
    try:
        GitHub(BASE, "tok").list_issue_comments("o/n", 7)
    except GitHubError as exc:
        assert "different host" in str(exc)
    else:
        raise AssertionError("expected GitHubError")


@respx.mock
def test_author_must_match_and_is_configurable(tmp_path, monkeypatch):
    other_bot = {"id": 2, "body": f"{MARKER}\nx", "user": {"type": "Bot", "login": "evil[bot]"}}
    mine = {"id": 8, "body": f"{MARKER}\nx", "user": {"type": "Bot", "login": "my-app[bot]"}}
    respx.get(f"{BASE}/repos/o/n/issues/7/comments").mock(
        return_value=httpx.Response(200, json=[other_bot, mine])
    )
    default = respx.post(f"{BASE}/repos/o/n/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 5})
    )
    assert _run(tmp_path, monkeypatch) == 0  # neither login is github-actions[bot]
    assert default.called
    patch = respx.patch(f"{BASE}/repos/o/n/issues/comments/8").mock(
        return_value=httpx.Response(200, json={})
    )
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    args = ["pr-comment", "--repo", "o/n", "--pr", "7", "--body-file", _body(tmp_path)]
    assert cli.main([*args, "--author", "my-app[bot]"]) == 0
    assert patch.called
