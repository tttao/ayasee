# Adopt ayasee in an existing repository

One page, one order: `init`, bootstrap, secrets, `doctor`, import, first pull request. Each step
links to the page that explains it; this page only says what to run and when. It is written for
the [Cloudflare and S3 profile](../../examples/cloudflare-s3/README.md), whose objects already exist
in production; the [Databricks on Azure profile](quickstart.md) follows the same order, with
identities and role assignments in place of tokens.

The steps that only a person can do are marked **(you)**: choosing reviewers, and creating provider
tokens. Everything else is a command.

## What you need

- A GitHub repository you administer, and `gh` logged in as an administrator. For the default
  gate it must be **public, or private on GitHub Enterprise** (Cloud or Server); a private
  repository on another plan uses `gate_mechanism: workflow`: see
  [below](#what-the-github-plan-must-allow).
- `tofu` (OpenTofu 1.10 or later), `terragrunt`, and `uv`.
- The release you adopt, `X.Y.Z`, from the [releases](https://github.com/tttao/ayasee/releases).
- **(you)** Two reviewer sets, different and non-empty: one approves prod applies, the other the
  break-glass path. GitHub team or user IDs.

### What the GitHub plan must allow

The gate ayasee relies on is made of GitHub settings, and a private repository does not get all of
them on every plan
([GitHub: deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)):

| Feature | Public repository | Private repository | What falls without it |
|---|---|---|---|
| Required reviewers on environments | every plan | GitHub Enterprise only | the prod gate (D9): nothing waits for an approval before prod; `prevent_self_review` (D2), the separate break-glass reviewers (D3) and the reviewer of `prod-pr-plan` go with it |
| Deployment branch policies | every plan | GitHub Pro, Team or Enterprise | the `main`-only environments: any branch could run a job in `prod` and read its secrets |
| Rulesets and branch protection | every plan | GitHub Pro, Team or Enterprise | the guard of the ledger token: it could push to `main` |

So the repository must be **public, or private on GitHub Enterprise**. A private repository on
GitHub Free, Pro or Team is **not supported by the default gate today**: ayasee says so instead of
running ungated. `ayasee init` warns before it scaffolds, `ayasee doctor` fails first with the
missing features, the bootstrap refuses to create the environments (a private repository states
its plan in `private_repository_plan`, which must be `enterprise`), and the staged and break-glass
runs refuse to go on when the gated environment has no required reviewers
(`ayasee require-reviewers`).

Such a repository can opt into **`gate_mechanism: workflow`** (decision
[D15](../governance/decisions.md#d15-the-workflow-gate)): ayasee, not GitHub, enforces the prod
gate, and every run says so as a DEVIATION. The staged run stops after `prod-plan` with an approval
issue, and a reviewer listed in `reviewers.prod` of `.ayasee.yaml` applies the plan with the
`iac-approve` workflow ([how](../concepts/staged-run.md#the-workflow-gate)). Set
`gate_mechanism = "workflow"` (and no reviewer variables) in the bootstrap as well, add the
`iac-approve` caller by hand, and know what the plan still gives you:

- **GitHub Pro or Team**: the environments stay restricted to `main` and keep their own secrets,
  so only a job on `main` reaches the prod secrets, and a ruleset on `main` can make every change
  to the reviewers or the callers go through a reviewed pull request.
- **GitHub Free**: no environment secrets, environment variables, branch policies or rulesets.
  Set `private_repository_plan = "free"`: the environments are created bare, and every secret and
  variable is a repository one. Any collaborator with write access can read the prod secrets from
  a workflow on any branch and push to `main`, and every stage reads the same provider tokens. The
  gate then stops mistakes and unreviewed applies, not a collaborator who means harm: keep the
  collaborators with write access to the reviewers.

## 1. Scaffold: `ayasee init`

At the root of your repository, on a branch:

```bash
uvx ayasee@X.Y.Z init --profile cloudflare-s3                          # stages test,prod
uvx ayasee@X.Y.Z init --profile cloudflare-s3 --iac-root infra/iac      # beside application code
```

It writes `.ayasee.yaml`, the callers under `.github/workflows/`, `root.hcl`, the bootstrap root and
one empty directory per stage, all pinned to the release commit of `X.Y.Z`: no placeholder to
search, no `sed`. The callers are `iac-pr.yml`, `iac-staged.yml`, `iac-nightly.yml` and
`iac-breakglass.yml`. It can be run again: a file with identical content is skipped, a file that
differs is shown as a diff and refused without `--force`, and nothing is written on a refusal. With
`--iac-root` it also sets `environment_prefix: iac-` in `.ayasee.yaml` and in the bootstrap, because a
repository that holds code already deploys it to environments called `test` and `prod`, and ayasee
must not share them ([below](#environments-that-already-exist)); `--environment-prefix other-`
chooses another prefix and `--environment-prefix ''` none (`--upgrade` moves the pin only and never
touches it). Fill the Cloudflare zone, account and tunnel IDs it leaves as placeholders. `--ref
<SHA>` is accepted only for the release commit of `X.Y.Z`; `doctor` (step 5) fails callers pinned to
an older release. When it can reach the repository (`--repo`, or the `origin` remote, with
`GITHUB_TOKEN` if set) it first checks [what the plan allows](#what-the-github-plan-must-allow)
and warns before writing anything; the scaffold is written either way. Details:
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

A private repository adds `-var private_repository_plan=enterprise`: without it the plan refuses
to create environments whose reviewers GitHub would not keep
([what the plan must allow](#what-the-github-plan-must-allow)).

It creates the eight environments (with the prefix, if any) with their reviewers and `main`-only deployment policies, sets
`AYASEE_LEDGER_URL` where the ledger is read or written, and, with `generate_plan_passphrase=true`,
generates `AYASEE_PLAN_PASSPHRASE` on `prod-plan`, `prod` and `prod-breakglass`. That passphrase is
then in the bootstrap's state: keep the state private (never commit it), or leave the option off
and set the passphrase yourself in step 4. See
[the plan passphrase](../../examples/cloudflare-s3/README.md#3-create-the-environment-secrets).

### Environments that already exist

Creating a GitHub environment is an upsert, so a bootstrap that declared your application's `test`
or `prod` would silently rewrite their reviewers and branch policy (a deploy from `develop` then fails
with a branch-policy error that names nothing of ayasee), and ayasee's secrets would sit next to the
application's. Two things stop that:

- **Use a prefix** (the default with `--iac-root`): `iac-test`, `iac-prod`, ... are ayasee's own.
  `ayasee doctor` warns when a workflow that is not an ayasee caller names one of ayasee's
  environments, which is the sign that you need it.
- **The bootstrap refuses** to plan when an environment it would create already exists and carries no
  `AYASEE_MANAGED` variable (the marker it sets on its own environments), and the error names the
  environment. To take one over deliberately (you want ayasee to own it, and nothing else deploys
  to it), apply once with `-var adopt_existing_environments=true`: the environment gets ayasee's
  reviewers, its `main`-only branch policy and the marker, and later plans pass without the flag. A
  bootstrap created before this check existed has no markers either: one apply with the flag writes
  them.

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
`AWS_SECRET_ACCESS_KEY`. It checks first that the repository's plan can hold the gate, and stops
there with one failure if it cannot. Then it lists, in one pass, everything steps 1 to 4 left out: an environment or
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

## Upgrade the pin

To move the callers and the bootstrap to another release, run `init --upgrade` at the root of the
repository with the new version:

```bash
uvx ayasee@X.Y.Z init --upgrade           # add --source owner/name for a mirror
```

It rewrites only the 40-character release SHA (and the `# ayasee vX.Y.Z` comment after it) in the
`uses: <source>/.github/workflows/iac-*.yml@<SHA>` lines of `.github/workflows/*.yml` and in the
`?ref=<SHA>` of the bootstrap's module sources. Triggers, `paths:`, secrets and everything else you
edited stay as they are; before writing it checks that the new text differs from the old one by
the pins alone. References to another source, and other actions, are not touched. `--ref <SHA>`
skips the release lookup. Read the release notes first: a new release may need changes the pin
does not carry.
