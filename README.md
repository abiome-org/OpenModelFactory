# Open Model Factory

Open Model Factory (OMF) is intended to be a **cloneable, local-first model
development workspace** that grows into a complete on-premises model factory.
Clone one repository, write your model and pipeline code in it, bring or
register your data, sync immutable data and model assets to storage you choose,
then train, evaluate, iterate, and deploy through one modular lifecycle.

OMF is not a model architecture or training algorithm. It provides the open
contracts and automation that turn replaceable data, training, inference,
evaluation, storage, and scheduling components into one reproducible factory.

## Status

This repository contains an executable `v1alpha1` reference implementation and
its normative specification. The local lifecycle is working end to end: project
bootstrap, typed modules, immutable data snapshots, resumable filesystem/S3
sync, workload DAGs, isolated subprocess execution, evaluation, signed events
and lineage, policy-gated releases, durable local deployment supervision, and
local/edge deployment packaging. The same service backs the Typer CLI and an
attributable, scoped, expiring-token FastAPI surface.

The maturity label remains **Alpha**, intentionally. Slurm and Kubernetes
adapters, signed federation primitives, capacity measurement, and air-gap
procedures are present, but this repository does not claim cluster, federated,
air-gap, or frontier conformance without the measured signed evidence required
by `SPEC.md`. In particular, no `OMF-Frontier` claim exists until a reproducible
test has run on at least 1,024 actual accelerators.

- [Specification](SPEC.md) — normative product, architecture, interface,
  conformance, and implementation requirements.
- [Operations runbook](docs/operations.md) — service, S3, backup/restore,
  incident, and offline-install procedures.

## Install

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e .
omf bootstrap
omf doctor
```

Run `make test-all` for lint, strict typing, and the full test suite. The project
also ships a multi-stage non-root `Dockerfile` and `compose.yaml` for an
authenticated local API service.

## North-star workflow

The following path uses only files included in a clean clone through training
and evaluation. Replace the example module, data, and workload with your own as
the project evolves.

```sh
git clone <repo-url> my-model-factory
cd my-model-factory

# Start a complete local factory and validate the host.
omf bootstrap --profile local
omf doctor

# Write arbitrary code under modules/, then validate its typed contracts.
omf module validate
omf module test

# Import the checked-in non-sensitive fixture as an immutable snapshot.
omf data add data/fixtures/numbers.jsonl --name example-numbers --mode copy \
  --rights data/fixtures/rights.yaml

# Sync it to a second local holding site. Replace this with S3 when needed.
omf store add secondary --driver filesystem --endpoint .omf/secondary-store
omf sync push dataset/example-numbers --to secondary --plan
omf sync push dataset/example-numbers --to secondary

# Run the scientific workload through the verified local binding.
omf run workloads/example-statistical.yaml --binding bindings/local.yaml

# Use the emitted runId to inspect and evaluate the run.
omf lineage show run:<run-id>/stage:train
omf evaluate run/<run-id>

# After an approved scanner writes a report covering the emitted model and
# admitted module digests, promote and package the release for edge use.
omf release create <run-id> --name candidate --intended-use research \
  --vulnerability-report reports/vulnerabilities.yaml --promote --approval reviewer
omf deploy deployments/example-edge.yaml
omf deployment status example-edge
# After applying a second deployment revision, use statusVersion as the guard:
omf deployment rollback example-edge --expected-version <status-version>
```

Commands through evaluation run directly from the clean clone. Release
promotion intentionally fails closed until an external scanner supplies current
vulnerability evidence; OMF does not fabricate it. `omf --help` and the OpenAPI
document expose the complete installed command/API surface.

## Self-contained operation

The local profile requires no hosted control plane, account, or call-home
service. SQLite, filesystem artifact storage, identity, secrets, events,
lineage, telemetry, subprocess execution, and deployment supervision are all
repository-scoped and initialized by `omf bootstrap`. Exact Python dependencies
are locked, and the same clone can be installed from a prepared wheelhouse in an
air-gapped environment. S3, cluster schedulers, federation, and external secret
services are optional bindings selected only when the installation needs them.

The repository is relocatable: absolute checkout paths are runtime metadata,
not resource identity. `.omf/` contains all generated local state and is ignored
by Git, so cloning into a new directory starts clean without losing the desired
state stored in manifests.

## Repository contract

The clone keeps code and desired state in Git while keeping large or
sensitive payloads in content-addressed stores:

```text
omf.yaml                 # project identity and defaults
factory/                 # open OMF implementation and SDK
modules/                 # all user model, data, train, eval, and runtime code
  models/
  objectives/
  transforms/
  generators/
  trainers/
  inference/
  environments/
  evaluators/
connectors/              # versioned data-source and holding-site declarations
data/                    # dataset manifests; payloads are ignored by default
workloads/               # scientific workload definitions
bindings/                # local, cluster, and federation resource mappings
policies/                # rights, safety, budget, promotion, and retention
deployments/             # batch, service, actor, edge, or control deployments
.omf/                    # recreatable cache and local runtime state; not in Git
```

Module manifests may point to code elsewhere in the repository, so this layout
does not constrain language or project organization.

## Source-of-truth boundaries

| Content | Source of truth |
| --- | --- |
| Code, workloads, modules, bindings, policies | Git repository |
| Data, checkpoints, model packages, releases | User-selected artifact stores |
| Derivation and decisions | Signed event and lineage records |
| Credentials and encryption keys | Local or site secret service, never Git |
| Local downloads and generated state | Rebuildable `.omf/` cache |

Data may be copied into OMF, registered in place, mounted/streamed from its
source, or replicated to one or more holding sites. Logical asset identity does
not change when physical storage changes.

## What the factory supplies automatically

For every admitted local run, the implementation:

1. validates module, data, binding, protocol, and namespace compatibility;
2. packages and executes immutable admitted module source and identifies inputs
   by digest;
3. syncs only missing verified chunks and commits manifests last;
4. provides a worker-count-independent replayable sampler;
5. executes model-neutral DAG stages through `omf.module/v1`;
6. publishes only independently verified atomic checkpoints;
7. materializes immutable evaluation evidence;
8. retains signed events, bidirectional lineage, telemetry, and review evidence;
9. emits an SPDX 2.3 SBOM and enforces rights, signatures, conformance,
   vulnerability evidence, approvals, and separation of duties at promotion;
10. creates a complete signed release and packages or starts an explicit
    deployment without a separate production fork.

Slurm/Kubernetes executor adapters and federation contracts are deliberately
separate from the verified local execution path. They expose deterministic
plans and scheduler lifecycle primitives, not a claim that `omf run` currently
executes a complete workload on those schedulers. A site binding is conformant
only after its integration passes the portable-workload, recovery, scheduling,
and scale scenarios in `SPEC.md`.

## Scale target

```diagram
┌────────────────┐      ┌─────────────────────┐      ┌──────────────────┐
│ Local binding  │─────▶│ Cluster binding     │─────▶│ Federated cells  │
│ process / host │      │ on-prem scheduler   │      │ frontier scale   │
└────────────────┘      └─────────────────────┘      └──────────────────┘
        same workload schema, asset identities, events, and lineage
```

Scale changes an explicit deployment binding, not pipeline code. A workload
that cannot physically fit on a small target may use offload or a shape-reduced
configuration, but it uses the same graph and interfaces.

## What “open” means here

An OMF-conformant implementation must be inspectable, replaceable, and operable
without a proprietary control plane. Required components use OSI-approved
licenses and have no SaaS or call-home dependency. Individual data and model
artifacts may have different rights, but those rights are machine-readable and
enforced at use, synchronization, and release gates.

No finite system scales infinitely. OMF targets **scale-invariant control
semantics** and requires every installation to publish measured capacity. A
factory also cannot make a model frontier-quality by itself; research choices,
data rights, algorithms, kernels, hardware, and scientific judgment remain
separate concerns.

## Verified reference path

The automated integration suite creates a clean temporary Git project, runs
idempotent bootstrap and doctor checks, imports rights-declared data, performs a
planned and actual incremental sync, validates and tests a module, trains and
evaluates it, creates and independently verifies a signed release, policy-gates
an alias promotion, and creates an edge deployment package. Additional tests
exercise interrupted sync, tamper detection, concurrent event ordering and
lineage cycle prevention, S3 semantics, subprocess environments, federation
restart/reconciliation, executor plans, checkpoint atomicity, and sampler
replay. See `tests/` and CI for executable evidence.
