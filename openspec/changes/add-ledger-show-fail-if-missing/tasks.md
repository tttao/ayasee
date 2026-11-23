## 1. Tests first

- [ ] 1.1 In `tests/test_ledger.py`, add four tests that call `ayasee.cli.main([...])` with `["ledger", "show", "--store", f"file://{tmp_path}", "--repo", "acme/payments", "--env", "prod", ...]` and use `capsys`: (a) no record, no flag: returns 0, stdout is `null\n`; (b) no record, `--fail-if-missing`: returns 1, stdout is empty, stderr contains `ayasee: no ledger record for acme/payments prod`; (c) `--history 5 --fail-if-missing` with no history: returns 1, same stderr; (d) a record written first with `open_store(f"file://{tmp_path}").append(rec)` (build `rec` the way existing tests in that file build a ledger record; search the file for an existing helper or `Record(` usage and reuse it), then `--fail-if-missing`: returns 0 and stdout parses as JSON with `"env": "prod"`.
- [ ] 1.2 Run `uv run pytest tests/test_ledger.py -q` and confirm tests (b), (c) fail (argparse rejects the unknown flag with exit 2) while (a) passes.

## 2. Implementation

- [ ] 2.1 In `src/ayasee/ledger.py`, in `_setup`, on the `show` subparser add `p.add_argument("--fail-if-missing", action="store_true", help="exit 1 when there is no record (or no history with --history)")`.
- [ ] 2.2 In `_show`, after computing `out` and before printing: if `args.fail_if_missing` and (`out is None` or `out == []`), print `f"ayasee: no ledger record for {args.repo} {args.env}"` to `sys.stderr` and `return 1`. Otherwise keep the existing print and `return 0`. Change nothing else.
- [ ] 2.3 Run `uv run pytest tests/test_ledger.py -q`: all tests pass.

## 3. Docs

- [ ] 3.1 In `docs/concepts/ledger.md`, section "Inspect it", add after the code block one sentence: "With `--fail-if-missing` the command exits 1 instead of printing `null` when the environment has no record (or no history with `--history`), so a script can test for a live record by exit code."
- [ ] 3.2 In `CHANGELOG.md`, under the `## [Unreleased]` heading (create it at the top of the version list, following the file's existing heading style, if it is missing), add under an `### Added` subheading: "- `ayasee ledger show --fail-if-missing` exits 1 when the environment has no record."

## 4. Checks

- [ ] 4.1 Run and make pass: `uv run ruff check`, `uv run ruff format --check`, `uv run pytest -m "not integration" -q`, `python scripts/check-links.py`.
