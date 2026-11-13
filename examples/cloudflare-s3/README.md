# Cloudflare and S3: reference adopter repository

A working layout for a small estate without a cloud identity provider: one host runs a `test` and a
`prod` stack behind one Cloudflare Tunnel, DNS and Zero Trust Access are on Cloudflare, and the
OpenTofu state is in an S3-compatible bucket. [ayasee](../../README.md) promotes `test` then
`prod`, with a gated prod apply of the exact approved plan. Copy this directory to a new
repository, replace the placeholders and follow the steps below.

This profile is new and **has not run end to end on GitHub yet**. The modules validate against the
Cloudflare provider and the tests in `tests/examples/test_cloudflare_s3.py` check the wiring; expect
to adjust it on a first real run.

```
.ayasee.yaml              stages [test, prod], identity: secrets, pbkdf2 plan key, shared_units
root.hcl                     S3-compatible backend, one bucket per stage, use_lockfile locking
shared/<unit>/main.tf        the OpenTofu code of each unit, written once
stages/<stage>/<unit>/       terragrunt.hcl (inputs) + .terraform.lock.hcl
bootstrap/                   one-off root: GitHub environments and the ledger variable
.github/workflows/           callers of the ayasee reusable workflows
```

| Unit | Stages | Resources |
|---|---|---|
| `dns` | `test`, `prod` | `cloudflare_dns_record`: the proxied CNAMEs of that stage's hostnames |
| `tunnel` | `prod` only, **shared** | `cloudflare_zero_trust_tunnel_cloudflared_config`: one ingress list with the test and the prod hostnames |
| `access` | `prod` only, **shared** | `cloudflare_zero_trust_access_application` and `cloudflare_zero_trust_access_policy`: who may open SSH to the host |

The DNS records split cleanly per stage. The tunnel ingress and the Access application do not: one
object serves both stages, so a test-only hostname change is a change to an object that carries
prod traffic. They are [shared units](../../docs/concepts/staged-run.md#units-that-serve-every-stage):
they live under `stages/prod` only, every change to them goes through the prod gate, and the gate
summary and the pull-request comment flag them as shared.

The HCL holds no credential. Zone, account and tunnel IDs are identifiers, not secrets, and are
committed as unit inputs. Everything uses placeholders: `OWNER`, the all-zero IDs, `example.com`,
`s3.example.invalid` and `ayasee-state`. Search for them before you start.

## Adoption steps

`ayasee init --profile cloudflare-s3` writes the configuration, the callers, `root.hcl` and the
bootstrap root of this example into your repository, with your stages and `iac_root`, pinned to one
release (step 5 is then done); it writes no unit. See
[start from the example](../../docs/guides/quickstart.md#1-start-from-the-example). The steps below
apply either way.

### 1. Bootstrap the environments

```bash
cd bootstrap
export GITHUB_TOKEN=...      # a token that can administer the repository; run this locally
tofu init
tofu apply -var github_owner=my-org -var github_repository=my-estate \
  -var 'prod_reviewers={teams=[101],users=[]}' -var 'breakglass_reviewers={teams=[202],users=[]}'
```

It creates `test-plan`, `test`, `prod-plan`, `prod`, `prod-breakglass`, `prod-pr-plan`, `drift-test`
and `drift-prod` (the pull-request plan environment is named after the first stage,
`<first stage>-plan`), and sets `AYASEE_LEDGER_URL` on every environment that touches the ledger (step 4).

Managing GitHub environments needs administration rights on the repository. Run this root from a
maintainer's machine, or from a workflow with a GitHub App or fine-grained token that has the
**Administration** permission, never with `github.token`, and never as an ayasee unit: a unit's
token would then be able to remove the gates that protect it.

### 2. Create the state buckets

One bucket per stage, created once by hand: `ayasee-state-test` and `ayasee-state-prod` (or your
prefix in `AYASEE_STATE_BUCKET_PREFIX`). Set `AYASEE_STATE_ENDPOINT` and `AYASEE_STATE_REGION`, or
replace the defaults in `root.hcl`. Locking uses `use_lockfile`, so the store must support
conditional writes; OpenTofu 1.10 or later. A store that ignores the condition gives no lock and no
error: run [the lock check](../../docs/guides/quickstart.md#check-that-the-store-honours-the-lock)
against each bucket before the first run.

### 3. Create the environment secrets

`identity: secrets` means provider tokens stored as **environment** secrets, one set per
environment, never at repository or organization level. Each environment gets only what its job
does. Cloudflare API tokens are scoped to zones and accounts, not to records: the `test` token can
edit every DNS record of the zone, the prod ones included. Keep it in `test` only, give it no tunnel
or Access permission, and use a separate zone for the test hostnames if that is not acceptable.

| Environment | `CLOUDFLARE_API_TOKEN` | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | `AYASEE_PLAN_PASSPHRASE` |
|---|---|---|---|
| `test-plan` (PR plan of `test`) | Zone DNS Read | read on `ayasee-state-test` | - |
| `prod-pr-plan` (PR plan of the shared units) | Zone DNS Read, Account Cloudflare Tunnel Read, Access: Apps and Policies Read | read on `ayasee-state-prod` | - |
| `test` | Zone DNS Edit | read and write on `ayasee-state-test` | - |
| `prod-plan` | Zone DNS Read, Cloudflare Tunnel Read, Access: Apps and Policies Read | read and write on `ayasee-state-prod` (the lock file) | yes |
| `prod`, `prod-breakglass` | Zone DNS Edit, Cloudflare Tunnel Edit, Access: Apps and Policies Edit | read and write on `ayasee-state-prod` | yes |
| `drift-test`, `drift-prod` | the read permissions of that stage | read and write on that stage's bucket (the lock file) | - |

```bash
passphrase="$(openssl rand -base64 48 | tr -d '\n')"
for env in prod-plan prod prod-breakglass; do
  gh secret set AYASEE_PLAN_PASSPHRASE --env "$env" --body "$passphrase"
done
gh secret set CLOUDFLARE_API_TOKEN --env test   # paste the test token; repeat per environment
```

The `test-plan` and `prod-pr-plan` tokens are used by pull-request code (same-repository pull
requests only; forks get no secrets). Keep them read-only. `prod-pr-plan` carries the prod
reviewers, so its token is used only after a reviewer approves the run
([shared units](../../docs/concepts/pr-checks.md#shared-units)).

### 4. The ledger, without a cloud

The ledger lives on an orphan branch, `ayasee-ledger`, of this repository. Writers use the
`git-branch://` store against the runner's checkout, so the URL holds the runner path; the bootstrap
sets it as the `AYASEE_LEDGER_URL` variable of every environment that reads or writes the ledger:

```
git-branch:///home/runner/work/<repo>/<repo>?branch=ayasee-ledger&remote=origin
```

(`/home/runner/work/<repo>/<repo>` is the checkout path on GitHub-hosted runners; pass
`repository_path` to the bootstrap for self-hosted ones.) The push needs a token, because the
workflows check out with `persist-credentials: false`: create a fine-grained token limited to this
repository with **Contents: read and write** and nothing else, and store it as the repository
secret `AYASEE_LEDGER_TOKEN`; the callers pass it as `ledger-token`. Protect `main` so that this
token cannot be used to change the code: it can still write any branch, so require pull requests on
`main` (a ruleset with no bypass). The staged and nightly runs check this with the ledger token and
warn when it is missing ([the ledger token and `main`](../../docs/concepts/staged-run.md#the-ledger-token-and-main));
with classic branch protection the check cannot see whether the token can bypass it, so use a
ruleset.

The cockpit reads the same branch through the contents API. Its `repos.yaml` line:

```yaml
- repo: my-org/my-estate
  ledger: github-branch://my-org/my-estate?branch=ayasee-ledger
  stages: [test, prod]
```

The cockpit tags the repository `secrets identity · deviation`, read from `.ayasee.yaml`.

### 5. Pin an ayasee release

Follow the [quickstart](../../docs/guides/quickstart.md#2-pin-an-ayasee-release): replace `OWNER`
and the all-zero SHA in `.github/workflows/*.yml` with `tttao/ayasee` and a release commit SHA.

### 6. Check before the first run

```bash
GITHUB_TOKEN="$(gh auth token)" ayasee doctor \
  --lock-check "s3://ayasee-state-test?region=$AYASEE_STATE_REGION&endpoint=$AYASEE_STATE_ENDPOINT" \
  --lock-check "s3://ayasee-state-prod?region=$AYASEE_STATE_REGION&endpoint=$AYASEE_STATE_ENDPOINT"
```

It lists, in one pass, what steps 1 to 5 left out: a missing environment or reviewer, a provider
secret missing from an environment or set at repository level, a missing passphrase or
`AYASEE_LEDGER_URL`, a caller still on the placeholder pin, no pull-request ruleset on `main`, and a
store that ignores the lock condition. See
[check it all](../../docs/guides/quickstart.md#check-it-all-before-the-first-run).

### 7. First adoption

Each unit reads existing objects; import them before the first apply so that ayasee does not try to
create a second tunnel configuration or Access application (`import` blocks in the unit, landed in a
pull request whose plan shows only imports). Land `dns` first, then `tunnel`, then `access`, each in
its own merge: the pull request of each shared unit shows its prod plan, and its first prod apply is
the one to watch.

## In a monorepo

This directory puts the estate at the repository root. When it sits beside application code, move
`root.hcl`, `shared/` and `stages/` under one directory, keep `.ayasee.yaml` and
`.github/workflows/` at the repository root, and set both keys:

```
.ayasee.yaml                  iac_root: infra/iac, units_root: infra/iac/stages
.github/workflows/            the callers, unchanged
infra/iac/root.hcl
infra/iac/shared/<unit>/main.tf
infra/iac/stages/<stage>/<unit>/
apps/...                      application code, ignored by ayasee
```

```yaml
iac_root: infra/iac
units_root: infra/iac/stages   # from the repository root, not from iac_root
```

`units_root` must be inside `iac_root`. `units_root: stages`, or no `units_root` at all, fails every
load of `.ayasee.yaml` with an error that says which path to write if the value was meant relative
to `iac_root`, and the first job of every workflow stops on it. The units find `root.hcl` with
`find_in_parent_folders`, and `root.hcl` takes the stage and the unit from the last two directories,
so neither changes.

Do not add a `paths:` filter to `pr.yml` or `main.yml`. A pull request that touches only
application code gets "No IaC changes" and a green `iac-pr`, which can then stay a required check;
a push of application code to `main` gives a staged run (a login and a plan per stage) that plans
clean and, when prod is in sync, asks no approval. If an IaC change is waiting at the prod gate at
that moment, the application push supersedes it and reviewers approve it again in the newer run. A
filter would skip the required check, and on `main` it can leave an IaC commit unpromoted: see
[monorepos](../../docs/concepts/pr-checks.md#monorepos). With `iac_root` set, the format checks and
the scanners look only at `iac_root`, and Checkov skips the `github_actions` framework.

## What is not covered

- The tunnel itself and its credentials are created once outside ayasee (`cloudflared tunnel
  create`); the `tunnel` unit manages only its ingress configuration.
- Deploying the applications behind the tunnel, and configuring the host, are out of scope.
- The ledger is the `git-branch://` store above. An experimental `s3://` ledger store exists, but
  only from the CLI: it is not wired into the reusable workflows and the cockpit cannot read it
  (see [the s3 store](../../docs/concepts/ledger.md#the-s3-store)).
