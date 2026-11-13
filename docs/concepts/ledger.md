# The ledger

The ledger answers one question: **what is live in each environment of each repository?** It is the
source of truth for that (D5). The guard reads it to decide whether a stage may run, the nightly
drift check reads it to find the SHA to plan, and the cockpit reads it to show the live SHA.

The code is `src/ayasee/ledger.py`. Records carry unit names, statuses and booleans only, never
plan values.

## Why not the Deployments API

GitHub Deployments is convenient but weak as a source of truth. Any token with `repo` or
`repo_deployment` scope can create, inactivate or delete deployment records, and GitHub documents no
retention or immutability for them. A deployment record shows that a job referencing the environment
ran, not that the state converged, and it cannot say that one unit of three failed. The ledger is
written by the stage's own identity to the backend, next to the state it describes. Deployments is
written **as a projection** for the GitHub UI, see
[Deployments as a projection](#deployments-as-a-projection).

## The record

One record is written when a stage finishes:

| Field | Meaning |
|---|---|
| `repo`, `env`, `sha` | Repository (`owner/name`), environment, the commit that was applied |
| `run_id`, `run_attempt` | The workflow run that wrote it |
| `units` | Per unit: `status` (`applied`, `no_changes`, `failed`, `skipped`) and `converged` (the post-apply re-plan was empty) |
| `converged` | True only if every unit converged |
| `approver` | The distinct approved logins of the environment gate, sorted and comma-joined, or null (R36) |
| `breakglass_incident` | The incident reference of a break-glass deployment, or null |
| `recorded_at` | RFC 3339 UTC with microseconds |

A failed apply is still written (the units that did apply are recorded) and then the job fails.
An empty prod plan is recorded too: `prod-in-sync` writes every unit as `no_changes` and converged
at the new SHA, so drift checks never keep planning an older SHA. Empty `units` are refused.

## Stores and URLs

`ledger.url` in `.ayasee.yaml`, or the environment variable `AYASEE_LEDGER_URL`, selects the
store. The variable on a GitHub environment wins over the file; the run fails if neither is set.

| URL | Store | Access | Use it for |
|---|---|---|---|
| `azure-blob://<account>.blob.core.windows.net/<container>?prefix=<prefix>` | Blob container | Read and write; `DefaultAzureCredential` (OIDC in CI); needs the `ayasee[azure]` extra | The default for Azure adopters. Blobs carry the index tag `ayasee-sha`. |
| `git-branch:///abs/path/to/repo?branch=ayasee-ledger&remote=origin` | Orphan branch of a git repository | Read and write | Adopters without a storage account. A CI run that writes it needs a token with `contents: write` (the optional `ledger-token` secret, R32); the staged and nightly runs warn unless `main` is behind a ruleset that token cannot bypass ([details](staged-run.md#the-ledger-token-and-main)). |
| `s3://<bucket>?region=<region>&prefix=<prefix>&endpoint=https://<host>` | S3 bucket, or an S3-compatible store with `endpoint` | Read and write; credentials from `AWS_*` environment variables (in the workflows, the dedicated `AYASEE_LEDGER_AWS_*` key); no extra needed. **Experimental**: not yet run against a real S3 or R2 bucket, see [the s3 store](#the-s3-store) | Adopters on AWS or an S3-compatible store. An `endpoint=` comes only from the `AYASEE_LEDGER_URL` variable. |
| `github-branch://<owner>/<repo>?branch=ayasee-ledger` | The same branch, read through the contents API | **Read only** | The cockpit. `append` always raises. |
| `file:///abs/path` | A directory | Read and write | Tests and local trials. |

Defaults: `prefix` is `ayasee-ledger`; `branch` is `ayasee-ledger`.

### The git-branch store

The branch is written with git plumbing and a temporary index, so the caller's working tree, index
and `HEAD` are never touched. With `remote` set, each write does fetch, commit, push, and retries up
to 5 times when the push is rejected because someone else wrote first. It creates
`refs/heads/<branch>` and `refs/ayasee/remote/*` and refuses to run if the branch is checked out.
Commits are authored by `ayasee <ayasee@users.noreply.github.com>`.

### The s3 store

**Experimental.** The URL format and parameters may change. The store is tested against an
in-process fake S3 that checks every signature and the conditional headers
(`tests/test_ledger_s3.py`); it has not yet run against a real Amazon S3 or Cloudflare R2 bucket.

`s3://<bucket>` writes the same layout as the other stores into an S3 bucket. The client is
`src/ayasee/s3.py`: AWS Signature Version 4 with the standard library and `httpx`, which ayasee
already depends on, and only three calls (GetObject, PutObject, ListObjectsV2). It adds no package
to the hash-pinned install of the setup action. boto3 was rejected: it brings `botocore` (about
26 MB installed), `s3transfer`, `jmespath`, `python-dateutil`, `six` and `urllib3`, all
permissively licensed but a large surface for three requests.

| Parameter | Default | Meaning |
|---|---|---|
| `region` | `AWS_REGION`, then `AWS_DEFAULT_REGION` | Signing region. Required one way or the other; `auto` for Cloudflare R2. |
| `prefix` | `ayasee-ledger` | Key prefix, `/`-separated path segments. |
| `endpoint` | none | `https://host[:port]` of an S3-compatible store. With it, requests are path-style (`<endpoint>/<bucket>/<key>`); without it, virtual-hosted on AWS (`https://<bucket>.s3.<region>.amazonaws.com`), so a bucket name with dots needs an endpoint. Plain `http` is refused. |

Any other parameter is refused. Credentials are `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and,
for temporary credentials, `AWS_SESSION_TOKEN`, read from the environment and nowhere else: no
shared config file, no instance metadata, no SSO. Missing credentials, or a value that is not
printable ASCII, fail the command (the message names the variable, never its value). Errors name
the HTTP status and the S3 error code only, never a response body or a header. Response bodies are
capped on their decompressed size (1 MiB for an object, 16 MiB for a listing page), so a compressed
bomb is stopped too, and a listing that carries a DTD is refused before it is parsed. The key
needs `s3:GetObject` and `s3:PutObject` on `<prefix>/*` and `s3:ListBucket` on the bucket for that
prefix, see [the ledger key](#the-ledger-key).

**Writes are conditional.** The history entry is created with `If-None-Match: *`: if it already
exists, it is read back, and identical bytes (a retry) succeed while different bytes are an error.
`current.json` is read with its ETag and then replaced with `If-Match: <etag>`, or created with
`If-None-Match: *` when there is none. If another writer got there first (`412 Precondition Failed`,
or `409 ConditionalRequestConflict`, which S3 asks clients to retry), the store re-reads and decides
again under the monotonic rule below, up to 5 times, then fails. Amazon S3 supports both headers on
PutObject since 2024 ([conditional writes][s3-cond]).

S3-compatible stores differ. A store that answers a conditional PUT with an error (for example
`501 NotImplemented`) makes every ledger write fail, which is the safe outcome. A store that
**silently ignores** the headers turns `current.json` into last-writer-wins: two concurrent writers
could leave the older record in it. History entries have unique names and are not affected, and
reads still return the newest of `current.json` and the newest history entry
([R19](#r19-the-newest-of-rule)), so `current()` stays correct; only the stored `current.json` can be
stale. Check that your store implements `If-Match` and `If-None-Match` on PutObject before you rely
on it.

**The endpoint comes only from the variable.** An `s3://` URL with `endpoint=` (even an empty
one) in `ledger.url` of `.ayasee.yaml` is refused when the file is read (`ayasee config show` and
every other `config` command, so the `resolve` job fails): the file is code that a pull request can
change, and an endpoint chosen there would receive the signed ledger requests of the job. Put such a
URL in the `AYASEE_LEDGER_URL` variable of each environment. An `s3://` URL without an endpoint
(Amazon S3 itself) may stay in the file.

#### The ledger key

The reusable workflows give the store **its own key**, never the state key of the plan and apply
steps. Two optional secrets, `AYASEE_LEDGER_AWS_ACCESS_KEY_ID` and
`AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY`, are declared by `iac-staged`, `iac-nightly` and
`iac-breakglass` and reach only the steps that read or write the ledger, as `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` in step-level environment:

| Workflow | Steps |
|---|---|
| `iac-staged` | `guard` and `ledger` of every stage job, `guard` of `prod-plan`, `guard` and `ledger` of `prod` and `prod-in-sync` |
| `iac-nightly` | "Read what is live in this environment" of `drift-<stage>` |
| `iac-breakglass` | the rollback check ("Refuse a rollback unless allow-rollback is true") and `ledger` |

No other step sees them (`tests/workflows/test_ledger_keys.py`), and the guard and ledger steps get
no state or provider credential. `iac-pr` does not read the ledger and declares neither secret.
There is no session-token secret: a stored secret cannot hold temporary credentials, which expire.
Put the region in the URL (`?region=`); these steps set no `AWS_REGION`.

Create the pair as **environment secrets** of each environment that touches the ledger (the stage
environments, `prod-plan`, `prod`, `prod-breakglass`, and `drift-<stage>`), never at repository
or organisation level, and pass it by name:

```yaml
    secrets:
      AYASEE_LEDGER_AWS_ACCESS_KEY_ID: ${{ secrets.AYASEE_LEDGER_AWS_ACCESS_KEY_ID }}
      AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY: ${{ secrets.AYASEE_LEDGER_AWS_SECRET_ACCESS_KEY }}
```

The key needs nothing but the ledger prefix. For a writer (stage environments, `prod-plan`, which
writes the prod record from `prod-in-sync`, `prod`, `prod-breakglass`), as an IAM policy, with
`<bucket>` and `<prefix>` (default `ayasee-ledger`) of the URL:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::<bucket>/<prefix>/*"
    },
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<bucket>",
      "Condition": { "StringLike": { "s3:prefix": "<prefix>/*" } }
    }
  ]
}
```

The `drift-<stage>` environments only read: drop `s3:PutObject` there. The cockpit has its own
read-only key, see below. On Cloudflare R2, use an R2 API token with "Object Read & Write" (or
"Object Read only") on the ledger bucket alone; R2 tokens cannot be narrowed to a prefix, so give the
ledger a bucket of its own. Narrower still, a key per environment can be limited to
`<prefix>/<owner>/<repo>/<env>/*` for its writes, but the guard reads the predecessor stage's
record, so each key must still read the records of the stages before it.

**Readable by the cockpit, read-only.** `cockpit-collect` reads `s3://` ledgers with a key of its
own that may only read (`AYASEE_COCKPIT_AWS_*`), and the collector never writes a ledger
([cockpit setup](../cockpit/setup.md#s3-ledgers)).

[s3-cond]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html

### The github-branch store (read-only)

`github-branch://` exists so the collector can read the ledgers of many repositories without cloning
each. It lists `history/` and reads files through the contents API. Writers use `git-branch://`
against the same branch. A cockpit `repos.yaml` entry points at it, for example
`github-branch://acme/payments?branch=ayasee-ledger`. The collector's read token must be able to
read the contents of every repository named in such a URL, see [cockpit setup](../cockpit/setup.md).

## Layout

All stores use the same layout (`{prefix}` is `ayasee-ledger` unless an Azure or S3 URL overrides it):

```
{prefix}/{owner}/{name}/{env}/current.json
{prefix}/{owner}/{name}/{env}/history/{YYYYMMDDTHHMMSSffffffZ}-{sha[:12]}.json
```

History file names are fixed-width and sort lexicographically, so listing them gives the newest
first. Repository and environment names are validated as path segments (`..` is rejected).

A write appends the history entry **first**, then overwrites `current.json`. Writes are idempotent:
retrying the same record re-sends identical bytes and succeeds, while a history entry that already
exists with different content is an error.

## R19: the newest-of rule

`current()` does not trust `current.json` alone. It returns the **newer of `current.json` and the
newest history entry**, compared by `recorded_at`. Two things follow:

- A crash between the two writes (history written, `current.json` not) is healed on the next read,
  because the history entry is newer.
- `current.json` is kept monotonic. `append` only replaces it if the existing one is not strictly
  newer, so a late retry of an older run cannot move "live" backwards.

The cost is one extra directory listing per read.

## Who writes and who reads

| Actor | Access |
|---|---|
| pre-prod stage jobs (`stage-1` to `stage-4`) | Write their own environment's record |
| `prod` job | Writes the `prod` record (with the approver) |
| `prod-in-sync` (runs in `prod-plan`) | Writes the `prod` record for an empty plan. The `prod-plan` identity therefore needs write on the prod record, but it cannot apply (R31). |
| `iac-breakglass` | Writes the `prod` record with the incident reference |
| `guard` | Reads the predecessor stage's record |
| `iac-nightly` (`drift-<env>`) | Reads the live SHA |
| Cockpit collector | Reads every record, read-only |

The guard rules, in order:

1. The predecessor of `prod` and `prod-plan` is `uat` (`prod-plan` writes no record and is skipped
   when looking for a predecessor). If there is no recordable predecessor, the guard says
   `predecessor_missing` (R22).
2. A predecessor whose record is at a different SHA is `superseded` if it converged, otherwise
   `predecessor_missing`. A record at this SHA that did not converge is `predecessor_not_converged`.
3. For `prod-plan` and `prod`, an open `ayasee:stop-the-line` issue for any environment up to and
   including prod is `stop_the_line`.

Any infrastructure error fails closed with exit 1.

## Deployments as a projection

With `--mirror-deployments` (the `mirror-deployments` input of the ledger action), the ledger write
also creates a GitHub deployment and a status:

- `ref` is the **commit SHA**, not `main`. A branch name would resolve to the branch tip, which may
  be newer than what was applied. `payload` records the SHA and `branch = "main"`.
- `production_environment` is true for `prod`; the status is `success` or `failure` by convergence,
  with a log URL to the run.
- The mirror is refused unless `GITHUB_REF` is `refs/heads/main` (D5), so only runs from `main`
  produce records. PR plan jobs run in the separate `<first stage>-plan` environment (`dev-plan` by
  default) so they do not pollute the first stage's history.
- If the mirror fails after the ledger was written, the command reports "ledger written, but
  deployment mirror failed". The ledger is already correct.

Treat Deployments as an index for humans. Nothing in ayasee reads it as authority.

## Inspect it

```bash
ayasee ledger show --store "$AYASEE_LEDGER_URL" --repo acme/payments --env prod
ayasee ledger show --store "$AYASEE_LEDGER_URL" --repo acme/payments --env prod --history 10
```
