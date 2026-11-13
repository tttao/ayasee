# Contributing

Thank you for helping. ayasee is pre-alpha; the best contributions right now are bug reports,
accuracy fixes to the docs, tests, and small focused changes. For anything that changes a safety
default (plan binding, approval, the guard, what leaves a job), open an issue first: those behaviours
come from recorded design decisions ([D1 to D14](docs/governance/decisions.md)) and need a reason to
change.

## Ground rules

- **Clean room.** No third-party names, tickets, runner labels, secrets, brand assets or data, in code, tests,
  fixtures or docs. See [the provenance statement](docs/governance/PROVENANCE.md).
- **Fail closed.** An unreadable input, an API error or an unknown value must stop the run, not pass it.
- **No values in output.** Plan values, finding details and secrets must never reach a job log, summary,
  comment, card, SARIF or snapshot. If you touch such code, add a test that a sentinel value does not
  leak.
- **Docs match the code.** If behaviour changes, change the page that describes it in the same commit.

## Licence of contributions

ayasee is licensed under the [Apache License 2.0](LICENSE). By opening a pull request you agree
that your contribution is licensed under the same terms (section 5 of the licence) and state that you
have the right to submit it. Do not copy code from a project whose licence does not allow that. A new
dependency must carry a permissive licence (MIT, BSD, Apache-2.0, ISC or similar); see
[provenance and licence](docs/governance/PROVENANCE.md#dependencies).

## Development setup

You need Python 3.12 or later and [uv](https://docs.astral.sh/uv/). Linux amd64 is the supported
development platform.

```bash
git clone <your fork> && cd ayasee
uv sync                           # creates .venv with the dev group
scripts/install-dev-tools.sh      # opa, conftest, terragrunt, actionlint, gitleaks into .tools/bin (gitignored)
uv run pre-commit install         # the commit hooks (see below)
```

`scripts/install-dev-tools.sh` installs pinned versions and verifies SHA-256 checksums. Pass tool names
(`opa conftest terragrunt actionlint gitleaks`) to install only some. zizmor is not installed by the script; run
it with `uvx`. OpenTofu (`tofu`) must be on your `PATH` for the integration tests and the module tests.
Node.js 22 is needed only for `cockpit-site/`.

## Tests and checks

Run these before you open a pull request. CI runs the same.

```bash
uv run ruff check                                   # lint (line length 100)
uv run pytest -m "not integration"                  # unit tests, no cloud and no tofu
uv run pytest -m integration                        # needs tofu and terragrunt (see below)
uv run pytest                                       # everything
python scripts/check-links.py                       # offline relative-link and anchor check of the docs
.tools/bin/actionlint                               # workflow syntax
uvx zizmor@1.32.0 --offline .github/ actions/       # workflow security
.tools/bin/opa check --strict policy/ && .tools/bin/opa fmt --fail policy/ && .tools/bin/opa test policy/ -v
scripts/check-policy-fixture.sh                     # conftest on the canned plan
```

- **Integration tests** (`-m integration`) run the real plan and apply scripts against small
  Terragrunt fixtures with a local backend. They need `tofu` and `.tools/bin/terragrunt`
  (`scripts/install-dev-tools.sh terragrunt`). They do not touch any cloud.
- **Module tests:** `cd modules/<name> && tofu init -backend=false && tofu validate && tofu test`.
- **Cockpit site:** `cd cockpit-site && npm ci --ignore-scripts && npm run typecheck && npm test && npm run build`.
- **End-to-end scenarios** (`e2e/`) are unit-tested in the normal suite against fakes. Never run
  `python -m e2e.run` against a real repository; it refuses unless the repository name ends in `-sandbox`.
- Write the failing test first. A bug fix comes with a test that fails without the fix.

## Pre-commit hooks

`.pre-commit-config.yaml` runs fast checks on every commit, once `uv run pre-commit install` has set
up the hook ([prek](https://github.com/j178/prek) reads the same file). Run them over the whole
repository with `uv run pre-commit run --all-files`.

- **Secrets:** gitleaks on the staged changes (`scripts/check-secrets.sh --staged`, the repository's
  `.gitleaks.toml`, values redacted) and `detect-private-key`. The CI job `secret scan` runs the
  same gitleaks scan on every commit of a pull request (the whole history on a push to `main`), so a
  commit made without the hook is still caught; gitleaks' own `private-key` rule covers private keys
  there. `uv run pre-commit run gitleaks-history --hook-stage manual` scans the whole history.
- **Hygiene:** large files (over 500 KB), merge-conflict markers, YAML, JSON and TOML syntax,
  shebangs and executable bits, final newline, trailing whitespace, LF line endings, and no commit on
  `main`.
- **Repository checks:** `ruff check`, `ruff format --check`, shellcheck (warnings and errors),
  zizmor and actionlint on the workflows and actions, `tofu fmt -check`, `opa fmt` and
  `opa check --strict` on `policy/`, and `scripts/check-links.py`.
- **Before push (opt-in):** `uv run pre-commit install --hook-type pre-push` adds the unit tests
  (`pytest -m "not integration"`, about 20 seconds). Integration tests stay in CI.

The local hooks use the tools of `scripts/install-dev-tools.sh` (or `tofu` on your `PATH`) and fail with
the install command when one is missing; they never skip silently. Vendored files under
`website/javascripts/vendor/` are excluded, since their SHA-256 is recorded in
[provenance](docs/governance/PROVENANCE.md).

**Fake credentials in tests.** A test that needs a credential-shaped value lives under `tests/` or
`e2e/` and writes it as one token holding `ayasee` followed by `test` or `fixture`: an AWS key id
such as `AKIAAYASEETEST` followed by six more of `[A-Z2-7]`, or `ayasee-fixture-secret-marker`. Only
that combination is allowlisted in `.gitleaks.toml`. A marker next to a value, in a comment or in a
PEM label does not count, so a fake private key cannot be allowlisted: build one at run time.
Never add an allowlist by path alone or by value alone; an inline `gitleaks:allow` comment is
ignored.

**If the scan flags a real secret:**

1. Rotate it first (revoke the credential at its provider). Removing it from the branch does not
   remove it from history, and a pushed commit must be treated as public.
2. If the commit is only local, amend or rebase it out and commit again.
3. If it reached a pushed branch or `main`, the finding stays in history and every later full scan
   (each push to `main`) would fail on it. Once the secret is rotated, record the finding's
   `Fingerprint:` line from the scan output (`<commit>:<file>:<rule>:<line>`) in `.gitleaksignore`
   at the root, with a comment saying when it was rotated, in the same pull request. A fingerprint
   names one commit, so the same value committed again is still caught.

## Docs and links

Docs live in `docs/`, the root policy files and `examples/`. `scripts/check-links.py` validates
relative links and `#anchor` fragments offline, with GitHub's heading slugs, and
`python scripts/check-links.py --list-external` lists the external URLs it deliberately does not
fetch. A test runs it over the repository, so a broken link fails CI. Follow the style of the existing
pages: plain, concrete, and every behavioural claim backed by a file you can name.

The same Markdown is published as a site by the `docs` workflow. To build it locally:

```bash
uv run --group docs python scripts/build-docs-site.py
uv run --group docs mkdocs build --strict -f website/mkdocs.yml   # output in build/site
```

A new page must be added to `nav` in `website/mkdocs.yml` and, if it is outside `docs/`, to `PAGES`
in `scripts/build-docs-site.py`. The strict build fails on a page that is copied but missing from
`nav`, and on a broken link or anchor.

## Commit style

[Conventional Commits](https://www.conventionalcommits.org/): `type(scope): subject`, imperative, no
trailing full stop, subject under about 72 characters. Types in use: `feat`, `fix`, `docs`, `test`,
`chore`, `refactor`, `ci`. Scopes are usually the component, for example `fix(cockpit): ...`,
`feat(apply): ...`, `docs(example): ...`. Use the body to say **why**. Keep trailers that attribute
AI-assisted work (`Co-Authored-By:`) and do not rewrite other people's history.

## The SHA-pinning rule

Every `uses:` of a third-party action in a workflow or composite action is pinned to a **full
40-character commit SHA**, with the human-readable version in a trailing comment:

```yaml
- uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
```

Never `@v4`, `@main` or any other moving ref. Renovate (`helpers:pinGitHubActionDigests`) keeps the
digests current. The same applies to adopters: callers pin ayasee by commit SHA and never use
`secrets: inherit`. ayasee's own action references are the one exception inside this repository:
they carry the placeholder `OWNER/ayasee/actions/<x>@0000...0000 # AYASEE_REF`, which a release
rewrites with `scripts/set-ayasee-ref.sh`, and tests check that every placeholder shares one ref.
Binaries downloaded by scripts are pinned by version and verified by SHA-256. Pre-commit hook
repositories are pinned the same way: `rev:` is a full commit SHA with the tag in a `# frozen: vX`
comment (`uv run pre-commit autoupdate --freeze`), and Renovate's `pre-commit` manager keeps them current.

## Pull requests

Keep a pull request to one concern. Describe what changed and why, list the checks you ran, and note
any behaviour you could not verify (especially anything GHES-specific). Do not push tags or create
releases; maintainers do that.
