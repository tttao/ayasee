## 1. CLI

- [x] 1.1 Failing tests for `import-plan --plan-dir` (per-unit files, summary, no values, ambiguity, API error, unsafe paths)
- [x] 1.2 Implement `import-plan --plan-dir`
- [x] 1.3 Failing tests, then `ayasee import-target` (stage and unit validation, environment name)
- [x] 1.4 Failing tests, then `ayasee pr-comment --head-branch` and the `import-plan` marker

## 2. Workflow

- [x] 2.1 Failing structure tests for `iac-import.yml` (triggers, jobs, permissions, secrets per step, deletion, artifact, comment job, no expression in run)
- [x] 2.2 Write `.github/workflows/iac-import.yml`
- [x] 2.3 Add it to the cross-workflow tests (environment names, tool mirror)

## 3. init and doctor

- [x] 3.1 Failing tests, then the `iac-import.yml` caller in the cloudflare-s3 example and init profile
- [x] 3.2 Failing tests, then doctor: caller kind, trigger check, environment of its secrets

## 4. Docs

- [x] 4.1 docs/guides/adopt.md step 6, examples/cloudflare-s3 README "First adoption", architecture components, pr-checks section, SECURITY.md, D15 note
- [x] 4.2 CHANGELOG `[Unreleased]`

## 5. Close

- [x] 5.1 Run every check of CONTRIBUTING
- [x] 5.2 Validate and archive the change
