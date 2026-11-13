# Migrate from environment branches

This guide moves a repository that promotes infrastructure through long-lived `dev`, `uat` and `prod`
branches (one pull request per environment) to one trunk with the staged run of ayasee. It follows
a staged migration: **drain, cutover PR, no-op rehearsal, two-week bake, archive tags, delete the
branches**. Do one repository at a time, smallest traffic first, and finish each one before starting
the next.

Why migrate: with three long-lived branches, a large share of merged pull requests is promotion
ceremony, which the AWS DevOps guidance lists as an anti-pattern. One trunk with environment folders
and ordered stages in one pipeline is what AWS Prescriptive Guidance, Azure Well-Architected and DORA
describe. It is not universal: see the honest wording in [the comparison](../comparison.md#trunk-practice-the-honest-wording).

## Before you start

The migration assumes the code of every environment already lives in folders on the same branch:

```
stages/dev/<unit>/
stages/uat/<unit>/
stages/prod/<unit>/
```

and that the `uat` and `prod` branches exist only to trigger a run and to hold an approval. If the
branches carry different code, first converge them into the folders and merge, so that
`stages/<env>` is the only difference between environments. Do that before the steps below.

Checklist, per repository:

- [ ] The environments, identities and variables exist, see [the quickstart](quickstart.md#3-bootstrap-the-environments-and-identities).
      Existing `dev`, `uat` and `prod` environments get their stage's variables, a `main`-only
      deployment branch policy and, on `prod`, required reviewers with self-review and admin bypass
      off. `prod-plan` is a new environment. If an environment already holds secrets, find out who
      uses each one before you add the `main`-only policy, because a policy change can break an
      unrelated consumer.
- [ ] A ledger store and a Key Vault key exist, and `.ayasee.yaml` names the ledger.
- [ ] Drift and scanning are in place. They are independent of the branch model, so they can start
      before the cutover: the nightly workflow, Trivy and Checkov as required checks, with Checkov
      baselines for the findings you already have ([the baseline](../concepts/pr-checks.md#the-checkov-baseline)).
- [ ] Each unit has a committed `.terraform.lock.hcl`. Prod plans and applies must resolve the same
      provider versions.
- [ ] Teams (optional) and the cockpit (optional) are wired.

## 1. Drain until `main = uat = prod`

Stop opening new environment pull requests and let the open ones land, from `dev` to `prod`, until the
three branches point at the same commit:

```bash
git fetch origin
git rev-parse origin/main origin/uat origin/prod        # three identical SHAs
git diff --quiet origin/main origin/uat && git diff --quiet origin/main origin/prod && echo drained
```

Why: the first staged run plans every stage at the merge SHA. If prod still lags `main`, the first
run is not a rehearsal; it is a real promotion of everything that was waiting, behind a single gate.
Draining also removes any doubt about what "live" means when the ledger starts empty.

Exit: the SHAs are identical, no promotion pull request is open, and no apply is running.

## 2. The cutover pull request

One pull request to `main` that:

- adds the caller workflows (`pr.yml`, `main.yml`, `nightly.yml`, `breakglass.yml`) pinned to a
  ayasee release **by full SHA**, using [the example](../../examples/databricks-azure/.github/workflows/main.yml)
  as the template and never `secrets: inherit`,
- adds `.ayasee.yaml` (`stages: [dev, uat, prod]`, the ledger URL, `allow_cascade: false`),
- removes or disables the old workflows that fire on the `uat` and `prod` branches,
- updates the required status checks of `main` to the new checks.

Do **not** delete the `uat` and `prod` branches or their protection in this pull request. They are the
escape hatch.

Review it like any other change. The `iac-pr` workflow plans the dev units on this very pull request,
which already tells you whether the plan identity works.

## 3. No-op rehearsal

Merge the cutover pull request. Because `main = uat = prod`, the first `iac-staged` run should change
nothing. That is the rehearsal:

| Job | Expected |
|---|---|
| `dev`, `uat` | Plan shows no changes, converge check passes, ledger record written |
| `prod-plan` | `has-changes` is false |
| `prod-in-sync` | Writes the `prod` ledger record at this SHA. No approval is asked. |

Check:

```bash
ayasee ledger show --store "$AYASEE_LEDGER_URL" --repo OWNER/REPO --env dev
ayasee ledger show --store "$AYASEE_LEDGER_URL" --repo OWNER/REPO --env uat
ayasee ledger show --store "$AYASEE_LEDGER_URL" --repo OWNER/REPO --env prod
```

all three at the merge SHA. If any unit **does** show changes, you have found drift or a hidden
difference between the branches. Do not approve to make it go away: understand it, fix it in a pull
request, and rehearse again. Also confirm the guard, the Teams cards and a first nightly drift run.

If you cannot drain to a no-op (because production must keep changing), land the first real change as
the rehearsal, with a reviewer, and watch the full flow including the gate.

## 4. Two-week bake

Run real changes through the new path for two weeks. The old branches still exist but are not used.
Exit criteria:

- Several real changes went dev, uat, prod-plan, gate, prod with no manual intervention.
- Nightly drift ran for every environment and either found nothing or its findings were understood.
- A rejected gate, a stale plan and a redrive behaved as described in
  [the staged run](../concepts/staged-run.md); at least one reviewer other than the author approved.
- Nobody pushed to `uat` or `prod`.
- The break-glass path was rehearsed in a non-urgent window
  ([DORA mapping](../compliance/dora-art17.md#break-glass-in-practice)).

The prod gate stays `always`. A risky-only dial is a possible later option, but `gate: risky` is not
supported yet.

## 5. Archive tags

Keep the history of the retired branches under tags:

```bash
git tag archive/uat-$(date +%Y%m%d)  origin/uat
git tag archive/prod-$(date +%Y%m%d) origin/prod
git push origin 'refs/tags/archive/*'
```

## 6. Delete the branches

Only after the bake and the tags: delete branch protection (or the ruleset) of `uat` and `prod` first,
then the branches.

```bash
git push origin --delete uat prod
```

Then remove what only the old flow used: its environments and secrets, its workflows, its
documentation and the skills or runbooks that describe it.

## Escape hatch

- **Until the branches are deleted:** revert the cutover pull request. The branches still exist, so
  the old flow works again. The ledger records stay and do no harm.
- **After deletion:** recreate the branches from the archive tags:

  ```bash
  git push origin archive/uat-YYYYMMDD:refs/heads/uat archive/prod-YYYYMMDD:refs/heads/prod
  ```

  and re-apply their protection.
- **Rolling back a change** after the migration is a revert on `main`, which goes through the same
  staged run. A revert cannot undo a destroy of stateful resources, which is why the
  [risk gate](../concepts/risk-gate.md) orders critical changes first. For an emergency, use the
  break-glass path.

## What changes for people

| Before | After |
|---|---|
| One pull request per environment per change | One pull request per change |
| Approval in a pull request on `prod` | Approval of the `prod` environment, by someone other than the author |
| "What is live" is a branch tip | "What is live" is the [ledger](../concepts/ledger.md), shown in the cockpit |
| Drift found by accident | A nightly drift issue per environment; prod drift holds the next prod run |

## Applications on `develop`, infrastructure on `main`

A monorepo can keep a `feature → develop → main` flow for its applications while the IaC under
`iac_root` uses the one trunk. ayasee promotes from `main` only: `iac-staged` runs on a push to
`main`, and every environment except `dev-plan` and `prod-pr-plan` accepts deployments from `main`
only (`modules/github-environments`). An IaC change merged to `develop` therefore reaches the first
stage only when `develop` is merged to `main`, together with every other IaC change waiting there,
behind one prod gate; if one of them reads the outputs of another, `prod-plan` refuses the batch
([R52](../concepts/staged-run.md#dependencies-between-units-r52)) and they land again one at a time.
Let pull requests that only touch `iac_root` target `main`, and split a pull request that changes
both application and IaC code into two. Leave the callers without a `paths:` filter and with no
`branches:` filter on `pull_request`: `iac-pr` stays green with "No IaC changes" on application
pull requests, so it can be a required check on both branches, and it still plans an IaC change
that was opened against `develop` ([monorepos](../concepts/pr-checks.md#monorepos)).

## Related repositories that need a different path

A repository with no `uat` or `prod` branch, or whose stages differ in kind (for example organisation
or team scopes), is not a candidate for this migration. A repository that creates nothing any more is
better retired than cut over.
