# Adopt ayasee in an existing repository

One page, one order: `init`, bootstrap, secrets, `doctor`, import, first pull request. Each step
links to the page that explains it; this page only says what to run and when. It is written for
the [Cloudflare and S3 profile](../../examples/cloudflare-s3/README.md), whose objects already exist
in production; the [Databricks on Azure profile](quickstart.md) follows the same order, with
identities and role assignments in place of tokens.

The steps that only a person can do are marked **(you)**: choosing reviewers, and creating provider
tokens. Everything else is a command.

## What you need

- A GitHub repository you administer, and `gh` logged in as an administrator.
- `tofu` (OpenTofu 1.10 or later), `terragrunt`, and `uv`.
- The release you adopt, `X.Y.Z`, from the [releases](https://github.com/tttao/ayasee/releases).
- **(you)** Two reviewer sets, different and non-empty: one approves prod applies, the other the
  break-glass path. GitHub team or user IDs.

## 1. Scaffold: `ayasee init`

At the root of your repository, on a branch:

```bash
uvx ayasee@X.Y.Z init --profile cloudflare-s3                          # stages test,prod
uvx ayasee@X.Y.Z init --profile cloudflare-s3 --iac-root infra/iac      # beside application code
```

It writes `.ayasee.yaml`, the callers under `.github/workflows/`, `root.hcl`, the bootstrap root and
one empty directory per stage, all pinned to the release commit of `X.Y.Z`: no placeholder to
search, no `sed`. It never overwrites a file without `--force`. Fill the Cloudflare zone, account
and tunnel IDs it leaves as placeholders. Details:
[start from the example](quickstart.md#1-start-from-the-example).

## 2. Bootstrap the environments

From an administrator's machine, never from CI and never as an ayasee unit:

```bash
cd bootstrap                            # infra/iac/bootstrap in a monorepo
export GITHUB_TOKEN="$(gh auth token)"
tofu init
tofu apply -var github_owner=my-org -var github_repository=my-estate \
  -var 'prod_reviewers={teams=[101],users=[]}' -var 'breakglass_reviewers={teams=[202],users=[]}' \
  -var generate_plan_passphrase=true
```

It creates the eight environments with their reviewers and `main`-only deployment policies, sets
`AYASEE_LEDGER_URL` where the ledger is read or written, and, with `generate_plan_passphrase=true`,
generates `AYASEE_PLAN_PASSPHRASE` on `prod-plan`, `prod` and `prod-breakglass`. That passphrase is
then in the bootstrap's state: keep the state private (never commit it), or leave the option off
and set the passphrase yourself in step 4. See
[the plan passphrase](../../examples/cloudflare-s3/README.md#3-create-the-environment-secrets).

## 3. Create the state buckets **(you)**

One S3-compatible bucket per stage (`ayasee-state-test`, `ayasee-state-prod`), and a key pair per
environment scoped to its stage's bucket. Set `AYASEE_STATE_ENDPOINT` and `AYASEE_STATE_REGION`, or
write them into `root.hcl`. See
[create the state buckets](../../examples/cloudflare-s3/README.md#2-create-the-state-buckets).

## 4. Create the remaining secrets **(you)**

The bootstrap knows every secret it could not create, and prints one command per secret and
environment, with the scope the value needs as a comment and never a value:

```bash
tofu output -raw secret_commands
```

Create each token with the scope its line names, then run the lines **one at a time**: `gh secret
set` without `--body` prompts for the value, so it lands neither on a command line nor in a shell
history. Pasting the whole block would feed the next line to the first prompt. The scope per
environment is the [table of the example](../../examples/cloudflare-s3/README.md#3-create-the-environment-secrets).

The ledger token is one of them: a fine-grained token limited to this repository, Contents read and
write only. Protect `main` with a ruleset that requires pull requests and has no bypass, so that
this token cannot change code ([the ledger token and `main`](../concepts/staged-run.md#the-ledger-token-and-main)).

## 5. Check: `ayasee doctor`

Back at the repository root:

```bash
GITHUB_TOKEN="$(gh auth token)" uvx ayasee@X.Y.Z doctor \
  --lock-check "s3://ayasee-state-test?region=$AYASEE_STATE_REGION&endpoint=$AYASEE_STATE_ENDPOINT" \
  --lock-check "s3://ayasee-state-prod?region=$AYASEE_STATE_REGION&endpoint=$AYASEE_STATE_ENDPOINT"
```

with the credentials of a key that can write each bucket in `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`. It lists, in one pass, everything steps 1 to 4 left out: an environment or
a reviewer, a secret missing or set at repository level, a variable, a caller still unpinned, the
ruleset on `main`, a bucket that ignores the lock condition. Repeat until it exits 0. See
[check it all](quickstart.md#check-it-all-before-the-first-run).

## 6. Write the units and import what exists

Add the units under `stages/<stage>/<unit>/` and their code under `shared/<unit>/`, as in the
[example](../../examples/cloudflare-s3/README.md). Their objects already exist, so each unit starts
with `import` blocks, not creates. `ayasee import-plan` writes them from a plan of the unit, with GET
requests only and the read-only token of that stage's plan environment:

```bash
cd stages/test/dns
export CLOUDFLARE_API_TOKEN=...          # the read-only token of test-plan
terragrunt plan -out=adopt.tfplan && terragrunt show -json adopt.tfplan > adopt.json
uvx ayasee@X.Y.Z import-plan --plan-json adopt.json     # writes imports.tf
rm adopt.json && find .terragrunt-cache -name adopt.tfplan -delete
```

It never guesses: two live objects for one address, or one object for two addresses, writes nothing
and exits 1. A `skip` line is a resource to import by hand. See
[first adoption](../../examples/cloudflare-s3/README.md#7-first-adoption) for the matching rules.

## 7. The first pull request

Commit the scaffold, the units of **one** unit name across its stages (`dns` first), and their
`imports.tf`. The pull request runs `iac-pr`: checks, scanners, policies and a read-only plan, posted
as one comment. **Nothing is applied.** The plan must show `import` for each existing object and no
`create`; an `update` after an import is a difference between your code and the live object, to fix
in the code unless it is the change you want. See [pull-request checks](../concepts/pr-checks.md).

## 8. Merge, one unit at a time

Merge. `iac-staged` applies `test`, plans prod, and waits at the prod gate for a reviewer who is not
the person who merged. Wait until it is live in prod, then repeat steps 6 to 8 for `tunnel`, then
`access`. Their first prod apply is the one to watch: they are
[shared units](../concepts/staged-run.md#units-that-serve-every-stage), serving both stages. Remove
the `imports.tf` files in a later pull request, once the objects are in state.

When a step is red, see [if something is red](quickstart.md#if-something-is-red).
