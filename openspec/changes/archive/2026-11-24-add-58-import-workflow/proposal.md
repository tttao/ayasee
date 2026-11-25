## Why

Adopting an existing estate starts with `import` blocks, and `ayasee import-plan` only runs as a
local CLI step: the person needs the provider token and the state keys in their shell, the pinned
OpenTofu and Terragrunt, and must delete a plan file and its JSON afterwards. With
`identity: secrets` those credentials often exist only as GitHub secrets, so neither CI nor an
assistant can do this step (issue #58).

## What Changes

- New reusable workflow `iac-import`, dispatched by hand on a branch with a `stage` and an optional
  `unit` (empty: every unit of the stage). In one job, in the `<first stage>-plan` environment, it
  plans read-only exactly as `iac-pr` does, runs `ayasee import-plan` on the plan JSON, deletes the
  plan files and JSON before the job ends, and publishes only the `imports.tf` files and a
  values-free outcome summary as an artifact. A separate job that runs no branch code posts the
  summary on the branch's open pull request (optional). It never commits.
- Only the first stage is supported: it is the one stage whose plan environment already accepts
  every branch with read-only credentials. Any other stage is refused with a message that points
  to the local CLI.
- `ayasee import-plan --plan-dir --out-dir --unit-prefix --summary`: the whole plan directory of a
  stage, one `imports.tf` per unit at its repository path, and the Markdown summary.
- `ayasee import-target`: validates the `stage` and `unit` inputs against the configuration and
  names the environment.
- `ayasee pr-comment --head-branch` and the `import-plan` sticky marker.
- `ayasee init` scaffolds the `iac-import.yml` caller for the `cloudflare-s3` profile (the
  provider that has lookups); `ayasee doctor` checks the caller like the others and refuses one
  with a trigger other than `workflow_dispatch`.

## Capabilities

### New Capabilities
- `import-workflow`: the `iac-import` reusable workflow and its trust model.

### Modified Capabilities
- `adoption-tooling`: `import-plan` over a plan directory; init and doctor know the new caller.

## Impact

`.github/workflows/iac-import.yml` (new), `src/ayasee/importplan/`, `src/ayasee/importtarget.py`
(new), `src/ayasee/prcomment.py`, `src/ayasee/scaffold.py`, `src/ayasee/doctor.py`,
`examples/cloudflare-s3`, docs (adopt guide, pr-checks, architecture, SECURITY), CHANGELOG.
