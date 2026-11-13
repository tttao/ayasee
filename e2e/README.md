# End-to-end scenarios

These scenarios drive a **sandbox GitHub repository** through the real ayasee workflows. They push
commits, merge pull requests, approve or reject environment gates with a second identity and then
assert on the ledger, the issues, the job conclusions and the Teams cards.

> **Never run them against a real repository.** `python -m e2e.run` refuses unless `E2E_REPO` ends in
> `-sandbox`, because the scenarios merge to `main`, break things on purpose and edit state files.
> The fixture's state handling (below) must never be used in production.

The driver (`e2e/sandbox.py`) and every scenario are unit-tested in the normal suite (`tests/e2e/`)
against respx and an in-memory fake. Nothing in the suite touches GitHub.

## Ephemeral sandbox: one command, no cloud

To try ayasee out or demonstrate it, `e2e/ephemeral.py` creates a throwaway sandbox repository in
your GitHub account and brings dev, uat and prod in sync. It needs `git`, `uv` and the `gh` CLI logged in
with the `repo` and `workflow` scopes. It needs no released ayasee commit and no Azure account:

```bash
uv run python -m e2e.ephemeral up --use-gh-token --approve   # or: SANDBOX_CONTENTS_TOKEN=...
uv run python -m e2e.ephemeral pr --repo you/ayasee-20261109-101500-sandbox
uv run python -m e2e.ephemeral status --repo you/ayasee-20261109-101500-sandbox
gh auth refresh -h github.com -s delete_repo            # once, so that `down` may delete
uv run python -m e2e.ephemeral down --repo you/ayasee-20261109-101500-sandbox
```

`up` does the setup of sections 1 to 5 below in one go, then bootstraps the sandbox:

1. It creates a **public** repository `ayasee-<timestamp>-sandbox` (`--name` to choose, it must end
   in `-sandbox`) with the topic `ayasee-ephemeral`. It is public because environment reviewers on
   private repositories need a paid plan (`--private` if you have one). Workflow logs are public too,
   but the fixture holds no secrets and creates no cloud resources.
2. It pushes the fixture and this checkout's `actions/`, `src/`, `policy/` and `rules/`, without
   workflows. Then it creates the environments (prod and prod-breakglass gated by you, or by
   `--reviewer LOGIN`, all but dev-plan, the pull-request plan environment `<first stage>-plan`, limited to
   `main`) and the secrets `LEDGER_TOKEN`,
   `STATE_TOKEN`, `PLAN_PASSPHRASE` and, if `SANDBOX_TEAMS_WEBHOOK` is set, `TEAMS_WEBHOOK`.
   `prod-breakglass` also gets the variable `AYASEE_LEDGER_URL`, which break-glass requires.
3. It pushes the workflows with every action reference pinned to the first commit of the sandbox
   itself, so nightly and break-glass work too. The push starts the first staged run: dev, uat,
   prod-plan, then prod waits for your approval in the run's **Review deployments** dialog.
4. Once that run has succeeded it adds the `grants` units in a third commit and follows a second
   staged run. On an empty repository `grants` and the `base` whose outputs it reads would both
   change, and prod-plan refuses that as a cascading change (R52), so they land one after the
   other, the incremental bootstrap of the [reference example](../examples/databricks-azure/README.md).

`up` returns when dev, uat and prod are in sync, after about 15 minutes. It waits for you to approve
each prod gate, or approves them itself with `--approve` (you must be a reviewer). It re-runs failed
jobs twice per run (`--reruns`), because GitHub release downloads of the tools can fail for minutes
at a time, and gives up after 60 minutes per run (`--timeout`).

The sandbox copies of the reusable workflows are **rewritten for a cloudless sandbox**: the
`azure/login` steps are removed, and the prod plan is encrypted with the test-only
`AYASEE_TEST_PASSPHRASE` (from the `PLAN_PASSPHRASE` secret, through a sandbox-only
`plan-passphrase` workflow secret) instead of a Key Vault key. The workflows of this repository are
not changed. The rewrite fails if the workflows no longer have the expected shape.
It also commits a [Checkov baseline](../docs/concepts/pr-checks.md#the-checkov-baseline) that accepts
`CKV_GHA_7` on the two break-glass workflows, whose `workflow_dispatch` inputs are intended.

The ledger and the state sync push with a token that has contents write on the sandbox:
`SANDBOX_CONTENTS_TOKEN`, or with `--use-gh-token` the `gh` token itself. The `gh` token works on
every repository you own, so it is a convenience for a sandbox you delete afterwards, not a pattern
to copy. By default you push, merge and approve as the same person (`prevent_self_review` is off). To
run the scenarios against an ephemeral sandbox, pass the second identity as `--reviewer` and add
`--prevent-self-review`, then follow [section 6](#6-run).

A demo, after `up`: open the first run and show dev, uat and prod-plan with the change summary, then
approve prod. Run `pr`, show the plan comment on the pull request, merge it, approve prod again and
show the ledger on the `ayasee-ledger` branch. To show a drift issue, dispatch the `nightly`
workflow after editing a state file on `ayasee-state`.

`down` deletes only repositories that carry the `ayasee-ephemeral` topic and end in `-sandbox`.

## Scenarios

| Name | What it proves |
|---|---|
| `valid` | dev, uat, prod-plan and prod succeed; the ledger shows dev, uat and prod at the merge SHA |
| `scan_fail` | the PR check fails on an open security group; no staged run starts |
| `pr_plan_fail` | `plan-dev` fails on a unit that cannot plan; no staged run starts |
| `dev_apply_fail` | uat and prod are skipped; a stop-the-line issue is open |
| `gate_reject` | a rejected gate applies nothing and the `prod-result` job posts a `prod_rejected` card (R50) |
| `drift` | a manual prod state edit makes the nightly run open a drift issue with `ayasee:stop-the-line` |
| `drift_cleared` | undoing the edit closes the issue and posts `drift_cleared` |
| `supersession` | merge A, merge B (its prod-plan succeeds and uat is live at B) while A waits, approve A: A's prod job fails at the guard with `superseded`; B's prod then reaches its gate, is approved and applies |
| `stale_plan` | state changes between prod-plan and approval: the engine refuses the saved plan, the first unit's apply fails, nothing is applied and a `prod_failed` card is posted |
| `cascade` | R52: base and grants (which reads base's outputs) change in one merge; dev and uat converge, prod-plan refuses with `cascading change`, and a second commit restores the files |
| `empty_prod` | a change with nothing for prod skips the gate and still moves the prod ledger SHA |
| `solo` | with `solo_mode: true` the run is labelled NON-COMPLIANT, the maintainer who pushed approves prod and a break-glass themselves, the ledger names them as approver and the post-approval issue opens. Needs `prevent_self_review` off on `prod` and `prod-breakglass` (an ephemeral sandbox made without `--prevent-self-review`, or `solo_mode = true` in the module) |
| `breakglass` | a valid break-glass applies prod, opens a post-approval issue and records the incident in the ledger, with every prod unit in the record (no unit scope, R71) |

Scenarios run one after another and clean up after themselves (they close the issues they cause and
remove scratch units), but they share one sandbox, so do not run two `e2e` runs at once (the
workflow has a concurrency group).

## 1. Create the sandbox repository

1. Create an empty repository whose name **ends in `-sandbox`**, for example `ayasee-sandbox`
   (the fixture's ledger path assumes that name; see `e2e/fixture/.ayasee.yaml`).
2. Seed it. `e2e/seed.sh` copies the fixture, an in-repo copy of the ayasee actions and CLI and the
   `iac-pr` and `iac-staged` workflows (rewritten to `./actions` by `scripts/set-ayasee-ref.sh --local`,
   R33 option ii). It pushes nothing.

   ```bash
   e2e/seed.sh /tmp/sandbox <canonical-owner>/ayasee <release-sha>
   cd /tmp/sandbox && git init -b main && git add -A && git commit -m "seed" && git remote add origin ... && git push -u origin main
   ```

   `pr.yml` and `main.yml` call the in-repo copies (`./.github/workflows/iac-*.yml`).
   `nightly.yml` and `breakglass.yml` **cannot** use `./actions` (those workflows check out other
   code, which would replace it), so they call the canonical repository at `<release-sha>`. That commit
   must carry release-rewritten references (Task 25) and the `extra-env-secret` secret of the
   workflows, so `drift`, `drift_cleared` and `breakglass`
   need a released SHA; the other scenarios do not.
3. Repository settings: allow squash merging; no required status checks and no branch protection on
   `main` that blocks the maintainer from merging (the scenarios merge pull requests themselves).
4. Secrets (repository): `LEDGER_TOKEN` (fine-grained token, contents write on the sandbox repo; the
   git-branch ledger pushes with it), `STATE_TOKEN` (the same kind of token, for the state sync in
   section 5) and `TEAMS_WEBHOOK` (the webhook echo URL, below).
5. Azure: the workflows log in with `azure/login`, so each environment needs `AZURE_CLIENT_ID`,
   `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`. The fixture creates no cloud resources, so the
   identities need no role assignments; they only have to exist with the federated credentials from
   `modules/azure-stage-identity`.

## 2. Environments

Create them with `modules/github-environments` (Task 16) for the sandbox repository: `dev-plan`, `dev`,
`uat`, `prod`, `prod-plan`, `drift-dev|uat|prod` and `prod-breakglass`. Set `prod_reviewers` and
`breakglass_reviewers` to include the second identity (next section). The module sets
`prevent_self_review`, so the identity that pushes (`E2E_TOKEN`) can never approve; that is the point of
the second identity. Set `AYASEE_LEDGER_URL` on `prod-breakglass` (break-glass reads the ledger
URL from its environment only); on the other environments set it only if it differs from the
`.ayasee.yaml` value.

## 3. The second identity

`E2E_TOKEN` pushes and merges (a maintainer, for example a fine-grained token: contents, pull requests,
issues, actions, workflows write on the sandbox repo). Approvals come from a different principal so they
are independent: the `e2e` workflow mints an installation token from a GitHub App
(`actions/create-github-app-token`, pinned by SHA) with `actions: read` and `deployments: write`, and
passes it as `E2E_APPROVER_TOKEN`. Install the App on the sandbox repository only. The runner refuses
to start when the two tokens are equal.

The runner compares the two tokens' `/user` logins and refuses if they are the same user (an App
installation token has no `/user`, which counts as a different identity).

Caveat to check on the first real run: GitHub only lets users and teams be *required reviewers* of an
environment, and a bot may not qualify. If the App cannot approve, use a dedicated machine user (in the
reviewers team) and store its token as `E2E_APPROVER_TOKEN` instead of the App; `e2e.yml` would then drop
the token step.

## 4. The webhook echo

`notify` posts Adaptive Cards to `TEAMS_WEBHOOK` (it requires https). The scenarios read them back, so
the URL must be an echo that **stores every POST body and returns them as a JSON array on `GET` of the
same URL**. Any small service will do (a Cloudflare Worker or a function app with a key-value store, a
request bin with an API); set it as the sandbox secret `TEAMS_WEBHOOK` and as `E2E_WEBHOOK_URL` in the
`e2e` environment. Cards are matched by the commit (`Commit` fact, first 12 characters), so the echo
needs no reset between runs. Without `E2E_WEBHOOK_URL`, the scenarios that read cards fail. `E2E_WEBHOOK_URL` must be an https
URL. **Anyone with the URL can read the captured cards** (repository names, commit prefixes,
environments, counts, approver logins), so use an unguessable URL and a sandbox-only echo.

## 5. State persistence (sandbox only)

Staged jobs run on different runners, so Terraform state has to outlive a job. The sandbox has no cloud
storage, so `e2e/fixture/root.hcl` uses the **`local` backend** with files under
`.ayasee-e2e-state/<env>/<unit>.tfstate`, and Terragrunt hooks run `scripts/state-sync.sh`:
`pull` before init, plan and apply restores the files from the `ayasee-state` branch, `push` after
apply (also after a failed one) commits the changed file back. Why this and not a remote backend: an
`http` backend is not available in OpenTofu, and a cloud backend would need real infrastructure for a
test that creates none. This also gives the `drift` and `stale_plan` scenarios a way to edit state
without any cloud API (they commit to the `ayasee-state` branch).

It is **not safe** for real use: no locking beyond git's push race, state in a git branch, no
encryption at rest. **Never copy it to a production repository.**

The token reaches the hooks without widening the product workflows: `iac-staged`, `iac-nightly` and
`iac-breakglass` take an optional secret `extra-env-secret`, which they export as
`AYASEE_EXTRA_SECRET` to the plan and apply steps only (nothing else, and never in `iac-pr`). The
fixture callers `main.yml`, `nightly.yml` and `breakglass.yml` pass `secrets.STATE_TOKEN` into it;
`pr.yml` passes nothing. The secret is a sandbox-only escape hatch, leave it unset in real
repositories. `state-sync.sh` hands the credential to git through `GIT_CONFIG_COUNT`/`GIT_CONFIG_*`
(never on a command line) and fails closed on GitHub Actions when it is missing. Pull request plans
(R39: no write token) run against empty state and never read or write the branch; only their
failure or success matters to the scenarios.

## 6. Run

```bash
export E2E_REPO=owner/ayasee-sandbox
export E2E_TOKEN=...            # maintainer
export E2E_APPROVER_TOKEN=...   # second identity
export E2E_WEBHOOK_URL=https://echo.example/events
uv run python -m e2e.run --all            # or: --scenario supersession
```

CI: `.github/workflows/e2e.yml` (manual and nightly; only on the canonical repository; environment
`e2e` with secrets `E2E_TOKEN`, `E2E_APPROVER_APP_ID`, `E2E_APPROVER_APP_KEY`, `E2E_WEBHOOK_URL` and
variable `E2E_REPO`).

## Recorded runs

Per R8 these scenarios have not been run against a sandbox yet.

| Date | Scenario | Result | Run URL |
|---|---|---|---|
| not yet run, requires a sandbox | | | |
