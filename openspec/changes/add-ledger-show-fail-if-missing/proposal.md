## Why

`ayasee ledger show` prints `null` and exits 0 when an environment has no record, so a script that
uses it to check "is anything live in prod?" must parse the JSON to notice. A flag that turns the
missing record into a failure makes that check one exit code.

## What Changes

- New `--fail-if-missing` flag on `ayasee ledger show`: with it, a missing current record prints
  `ayasee: no ledger record for <repo> <env>` to stderr and exits 1. Without it, behaviour is
  unchanged (`null`, exit 0).
- With `--history N`, the flag fails when the history is empty.

## Capabilities

### New Capabilities

### Modified Capabilities
- `ledger`: adds a requirement for the `ledger show` command's handling of a missing record.

## Impact

`src/ayasee/ledger.py` (`_show`, the `show` subparser), `tests/test_ledger.py`,
`docs/concepts/ledger.md` ("Inspect it"), `CHANGELOG.md`.
