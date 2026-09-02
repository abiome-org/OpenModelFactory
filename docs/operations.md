# Operations runbook

## Install and bootstrap

Use the directory installer for a new or existing project. Inspect its complete
plan before allowing package downloads or local state creation:

```sh
./install.sh --plan /path/to/model-project
./install.sh /path/to/model-project
. /path/to/model-project/.venv/bin/activate
omf --project /path/to/model-project --output json agent context
```

The installer preserves existing manifests and appends rather than replaces an
existing `AGENTS.md` or `.gitignore`. It creates missing local desired state,
initializes Git only when needed, prints and applies the bootstrap plan, then
requires `omf doctor` and bounded agent context to succeed. Reinstallation
builds a fresh environment from the selected base interpreter and atomically
replaces only a `.venv` carrying the OMF installer marker; an unrelated
pre-existing `.venv` is rejected rather than executed or overwritten. Pip may
use the configured package index only for hash-locked binary runtime and build
dependencies; OMF itself builds without an isolated backend download. The plan
discloses that network effect. OMF creates no hosted account, uploads no project
metadata, and leaves call-home telemetry disabled.

For a manual development installation, use Python 3.11 or 3.12 in an isolated
environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --only-binary=:all: --require-hashes -r requirements.lock
python -m pip install --only-binary=:all: --require-hashes -r requirements.build.lock
python -m pip install --no-build-isolation --no-deps -e .
omf bootstrap
omf doctor
```

`omf bootstrap` is idempotent. It creates repository-local state under `.omf`
with a restrictive umask. Back up `.omf/identity` separately from artifact data
and restrict it to the factory operator. The local bearer token is encrypted in
the metadata database and is intentionally never printed by the CLI.

## Service operation

`docker compose up --build -d` starts the authenticated API after bootstrap.
Terminate TLS at the site's ingress or reverse proxy; the built-in server does
not provide TLS or mutual workload authentication. The bootstrap credential is
an all-scope local operator token; obtain it through an authorized operator
workflow, not logs or Git. Create attributable, expiring least-privilege
credentials with `omf token create --actor <identity> --scope read` and revoke
them with `omf token revoke <token-id>`. Token values are stored only as hashes
and are returned once at creation.

For direct service operation:

```sh
omf api serve --host 127.0.0.1 --port 8080
```

Use a process supervisor such as systemd, Kubernetes, or the site's scheduler.
Run one writer service per SQLite metadata database. WAL permits concurrent
readers, but a shared network filesystem must honor SQLite locking semantics.

## Executor readiness

Inventory provider code and inspect its source before authorizing it, then
preflight the exact binding and workload:

```sh
omf --output json executor list
omf --output json executor preflight bindings/site.yaml --workload workloads/train.yaml
```

Installed `omf.executors` entry points are trusted code loaded into the API
process. Unknown providers, missing protocol transport, unavailable scheduler
tools, and unenforceable isolation fail closed before OMF allocates a run. A
successful scheduler preflight is not scale conformance; retain measured
portable-workload, restart, cancellation, checkpoint, and scale evidence. See
[the executor provider guide](executors.md) for backend-specific requirements.

## Backup and restore

Create an online-consistent metadata backup:

```sh
omf backup /secure-backups/metadata-$(date +%Y%m%d).db
```

Also replicate content-addressed manifests/blobs and securely back up
`.omf/identity`. Test restoration in an isolated checkout:

1. stop writers;
2. retain the failed `.omf` directory for forensics;
3. restore identity keys with mode `0600`;
4. restore the SQLite backup as `.omf/metadata.db`;
5. restore or reconnect artifact stores;
6. run `omf doctor`, verify dataset snapshots, list active goals/knowledge, and
   inspect signed event tails;
7. resume deployments only after policy review.

Never restore only a signing key into a metadata history from another trust
domain.

## S3-compatible holding sites

Store credentials as an encrypted JSON secret with purpose
`artifact-store-credentials`. Accepted keys are `aws_access_key_id`,
`aws_secret_access_key`, `aws_session_token`, `region_name`, `use_ssl`, and
`verify`.

```sh
omf secret set primary \
  --purpose artifact-store-credentials \
  --value '{"aws_access_key_id":"…","aws_secret_access_key":"…"}'
omf store add primary --driver s3 --endpoint s3://bucket/prefix --secret-ref primary
omf sync push dataset/training-corpus --to primary --plan
omf sync push dataset/training-corpus --to primary
```

Use `--plan` before each production transfer. Sync never deletes destination
content and publishes the manifest only after every chunk verifies.

## Vulnerability evidence and release promotion

Promotion is fail-closed when vulnerability evidence is absent, invalid, does
not cover the aggregate model and admitted module artifacts, or
contains an unwaived open high/critical finding. Import a scanner's YAML/JSON
report with `omf release create --vulnerability-report <path>`. The report must
contain `scanner` (name/version object), `databaseRevision`, timezone-aware
`generatedAt`, `subjects` (OMF artifact digests), `findings`, and `waivers`.
Each finding contains `id`, `severity`, and `status`. OMF commits the report as
an immutable artifact and binds its summary into the signed release. A site is
responsible for obtaining and signing scanner databases in its connected or
air-gapped supply-chain process.

## Deployment lifecycle and rollback

Deployment commands run through the explicit executor provider recorded in the
immutable deployment revision (local by default). Inspect and cancel them with
`omf deployment status <name>` and
`omf deployment cancel <name>`. Each status response includes `statusVersion`;
use that value as the compare-and-swap guard when restoring the previous
immutable deployment revision:

```sh
omf deployment rollback <name> --expected-version <status-version>
```

A stale version is rejected rather than overwriting a concurrent deployment
change. Before starting any deployment, OMF verifies the release signature and
requires its recorded promotion policy decision to be `allow`.

## Air-gapped installation

On a connected build host, build wheels for OMF and every dependency, generate
an inventory and checksums, scan them under site policy, and sign the bundle.
Transfer through the approved media process. In the isolated environment,
verify signatures and hashes and install with:

```sh
python -m pip install --no-index --find-links ./wheels open-model-factory
```

Run with network namespace denial or a site sandbox and verify no external
traffic. An air-gap conformance claim additionally requires the signed scenario
evidence specified in `SPEC.md`; offline installation alone is not a claim.

## Incident and recovery rules

- Quarantine, do not mutate, suspect artifacts.
- Revoke compromised credentials and rotate site trust deliberately; historical
  signatures remain bound to the old key.
- Preserve event, lineage, scheduler, and ingress logs under retention policy.
- Preserve goal statuses and evidence-backed knowledge with metadata backups;
  never reconstruct them from chat logs.
- A failed or incomplete checkpoint is never a restore target.
- A stale running operation is reconciled only from an immutable `RunResult`.
  Without one, OMF marks the outcome indeterminate and does not automatically
  replay potentially non-idempotent module work. Inspect it with
  `omf operation get <operation-id>`, then run
  `omf operation reconcile <operation-id>` under the original actor identity.
- Alias and deployment changes require a recorded passing policy decision.
- Do not claim `OMF-Frontier` without a reproducible report from at least 1,024
  actual accelerators.
