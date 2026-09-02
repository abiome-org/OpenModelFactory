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

Execution is selected through an explicit provider registry. Bindings can use
built-in local/Slurm/Kubernetes adapters or trusted installed providers for
Modal, Vast.ai, site schedulers, and future runners without changing a workload.
Provider inventory, configuration contracts, and fail-closed preflight are
available to both agents and operators.

The maturity label remains **Alpha**, intentionally. The checked-in path is a
tested local reference, not an `OMF-Core` conformance claim: normative sampler
replay, interrupted checkpoint restore, and two-adapter train/serve parity remain
fail-closed extension boundaries. Slurm and Kubernetes adapters, signed federation
primitives, capacity measurement, and air-gap procedures are present, but this
repository does not claim cluster, federated, air-gap, or frontier conformance
without the measured signed evidence required by `SPEC.md`. In particular, no
`OMF-Frontier` claim exists until a reproducible test has run on at least 1,024
actual accelerators.

- [Specification](SPEC.md) — normative product, architecture, interface,
  conformance, and implementation requirements.
- [Model-building manual](manual/README.md) — progressive, status-labeled
  workflows for modules, data, evaluation, training, experiments, RLVR design,
  execution, and release.
- [Operations runbook](docs/operations.md) — service, S3, backup/restore,
  incident, and offline-install procedures.
- [Executor provider guide](docs/executors.md) — portable workload boundary,
  plugin contract, backend guidance, and conformance checklist.
- [Architecture map](docs/architecture.md) — concise ownership and extension
  boundaries without duplicating the normative specification.

## Install into a directory

After cloning this distribution, inspect the non-mutating plan and install it
into the clone or another project directory:

```sh
./install.sh --plan /path/to/model-project
./install.sh /path/to/model-project
cd /path/to/model-project
. .venv/bin/activate
omf --output json agent context
```

The installer requires Python 3.11 or newer. It creates an isolated `.venv`,
installs hash-locked runtime dependencies and this exact OMF source, and on
reinstall atomically rebuilds only a `.venv` previously marked as OMF-managed.
It generates only missing project manifests and workspace directories,
initializes Git when needed, plans and applies local bootstrap, and verifies
both `omf doctor` and the agent context. It never overwrites existing manifests
or an unrelated pre-existing `.venv`. Existing `AGENTS.md` and `.gitignore`
files are preserved and receive one idempotent, managed OMF section.

For the most self-contained, directly editable workspace, clone this repository
at the desired project path and run `./install.sh .`. The installed
`AGENTS.md` section is an operator runbook for agents: observe, state intent,
plan/preflight, execute, verify evidence, and accrete knowledge. It is ordinary
Markdown in the uppercase root location defined by the
[AGENTS.md standard](https://agents.md/); nearest nested guides can specialize
subtrees.

## Manual development install

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --only-binary=:all: --require-hashes -r requirements.lock
python -m pip install --only-binary=:all: --require-hashes -r requirements.build.lock
python -m pip install --no-build-isolation --no-deps -e .
omf bootstrap
omf doctor
```

Run `make test-all` for lint, strict typing, and the full test suite. The project
also ships a multi-stage non-root `Dockerfile` and `compose.yaml` for an
authenticated local API service.

## North-star workflow

After installing the clone as described above, the following path uses only
checked-in files through training and evaluation. Replace the example module,
data, and workload with your own as the project evolves.

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
omf data add data/fixtures/affine.jsonl --name example-affine --mode copy \
  --rights data/fixtures/rights.yaml

# Commit the portable package, evaluation, and mix contracts.
omf resource apply model-packages/example-affine.yaml
omf resource apply evaluations/example-affine.yaml
omf resource apply mixes/example-affine.yaml

# Sync it to a second local holding site. Replace this with S3 when needed.
omf store add secondary --driver filesystem --endpoint .omf/secondary-store
omf sync push dataset/example-affine --to secondary --plan
omf sync push dataset/example-affine --to secondary

# Run the scientific workload through the verified local binding.
omf executor preflight bindings/local.yaml --workload workloads/example-from-scratch.yaml
omf run workloads/example-from-scratch.yaml --binding bindings/local.yaml

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

## Agent control loop

OMF presents agents with a control loop rather than asking them to infer state
from logs or replay an entire conversation. The same first command works before
and after bootstrap:

```sh
# Use JSON for a stable machine interface. Before bootstrap this includes the
# exact repository-scoped bootstrap plan; afterward it is the live situation.
omf --output json agent context --limit 10 --max-bytes 65536

# Discover every agent-facing action's CLI/API mapping, scope, preconditions,
# effects, plan support, idempotency, risk, and cost class.
omf --output json agent capabilities

# Make intent explicit and guard later state changes against stale observers.
omf goal create quality --objective "Improve held-out quality" \
  --success "accuracy >= 0.90" --constraint "gpuHours <= 100" \
  --budget gpuHours=100 --priority 80
omf goal status quality --state blocked --expected-version 1 \
  --reason "waiting for dataset rights review"

# Accrete a claim only with evidence. A correction names the exact knowledge it
# supersedes, preserving rather than rewriting history.
omf knowledge record baseline --category observation \
  --claim "evaluation/42 measured accuracy 0.81" --confidence 0.98 \
  --evidence evaluation/42 --goal-ref goal/quality --tag quality
omf knowledge record baseline-corrected --category observation \
  --claim "the corrected measurement is 0.83" --confidence 1 \
  --evidence evaluation/43 --supersedes knowledge/baseline
```

An `AgentContext` contains readiness, active goals, per-kind and
executor-provider inventory, recent run/deployment/operation status, active
knowledge, payload-free event summaries, global blockers, and deterministic
recommended actions. Every recommendation states why it exists, its exact
command template, preconditions, expected effects, approval/destructive flags,
idempotency semantics, and a conservative cost class. Recommendations never
execute themselves and do not replace policy or scientific judgment.

The view is bounded by item and byte budgets. `viewDigest` covers the projection
except its own field and `generatedAt`; HTTP serves it as an ETag, and
`recentEvents.cursor` can be passed back with `--since` for incremental
refreshes. Event summaries omit payloads, operation summaries omit
requests/results, and secrets are never included. A focus narrows detail but
never hides a global blocker or changes the facts used to recommend an action.
See the [agent control guide](docs/agent-control.md) for the complete contract.

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
model-packages/          # canonical model signatures, adapters, and conformance vectors
evaluations/             # versioned evaluation protocols and thresholds
mixes/                   # portable data-mixture and replay intent
workloads/               # scientific workload definitions
bindings/                # local, cluster, and federation resource mappings
policies/                # rights, safety, budget, promotion, and retention
deployments/             # batch, service, actor, edge, or control deployments
starter-packs/           # optional framework/model/modality conveniences
.omf/                    # recreatable cache and local runtime state; not in Git
```

Module manifests may point to code elsewhere in the repository, so this layout
does not constrain language or project organization.

## Source-of-truth boundaries

| Content | Source of truth |
| --- | --- |
| Code, desired-state manifests, model definitions, bindings, policies | Git repository |
| Data payloads, checkpoints, built model payloads, releases | User-selected artifact stores |
| Derivation and decisions | Signed event and lineage records |
| Goals and evidence-backed accumulated knowledge | Immutable resources, CAS status, signed events, and lineage |
| Credentials and encryption keys | Local or site secret service, never Git |
| Local downloads and generated state | Rebuildable `.omf/` cache |

Data may be copied into OMF, registered in place, mounted/streamed from its
source, or replicated to one or more holding sites. Logical asset identity does
not change when physical storage changes.

## What the factory supplies automatically

For every admitted run, the implementation:

1. validates module, data, binding, protocol, and namespace compatibility;
2. packages and executes immutable admitted module source and identifies inputs
   by digest;
3. independently verifies copied input artifacts before materialization;
4. executes model-neutral DAG stages through `omf.module/v1`;
5. atomically publishes a checkpoint when a checkpoint-capable module emits
   verified state and shard artifacts;
6. materializes immutable evaluation evidence;
7. retains signed events, bidirectional lineage, telemetry, and review evidence;
8. emits an SPDX 2.3 SBOM and enforces rights, signatures, conformance,
   vulnerability evidence, approvals, and separation of duties at promotion;
9. creates a complete signed release and packages or starts an explicit
    deployment without a separate production fork.

The executor registry resolves the binding exactly; unknown providers and
missing protocol transport fail before a run is allocated and never fall back
to local. Slurm has shared-filesystem module transport but remains
scheduler-lifecycle-only until an installed provider attests the execution
environment and required isolation. The
built-in Kubernetes adapter exposes deterministic Job/JobSet lifecycle
primitives but intentionally does not claim module source, request/result, or
artifact transport. Trusted entry-point packages can provide Modal, Vast.ai,
complete Kubernetes, or site-specific runners. See
[executor providers](docs/executors.md). A site binding is conformant only after
its integration passes the portable-workload, recovery, scheduling, and scale
scenarios in `SPEC.md`.

## Scale target

```diagram
┌────────────────┐      ┌─────────────────────┐      ┌──────────────────┐
│ Local binding  │─────▶│ Cluster binding     │─────▶│ Federated cells  │
│ process / host │      │ on-prem scheduler   │      │ frontier scale   │
└────────────────┘      └─────────────────────┘      └──────────────────┘
        same workload schema, asset identities, events, and lineage
```

Scale changes an explicit binding/provider, not pipeline code. A workload
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
restart/reconciliation, executor plans, role-mapped atomic checkpoint
publication, and the standalone deterministic sampler. The reference checkpoint
explicitly does not claim sampler replay or restore conformance. See `tests/` and
CI for executable evidence.
