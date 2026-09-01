# Open Model Factory Specification

**Version:** 0.1.0-draft  
**Date:** 2026-09-01  
**Status:** Draft specification with an executable `v1alpha1` reference
implementation. No conformance profile is claimed without the measured, signed
evidence required by Section 21.

## 1. Abstract

Open Model Factory (OMF) specifies a reproducible, model-agnostic system for
turning data, environments, code, and research decisions into evaluated and
deployable model releases. It covers ingestion, curation, synthesis, sampling,
training, post-training, reinforcement learning, evaluation, serving,
observation, governance, and release.

The reference distribution is a cloneable project workspace. A user starts
with one Git clone, writes arbitrary modules in the repository, imports or
registers data, chooses one or more artifact holding sites, and runs the factory
locally without first assembling external platform services. The same project
then moves to cluster or federated execution through bindings rather than code
or workflow forks.

OMF separates scientific intent from deployment mechanics. A scientific
`WorkloadSpec` is stable; a replaceable `Binding` maps it to a process, an
on-premises cluster, or a federation of cells. Every stage consumes and produces
immutable, attributable assets under one event and provenance model.

The specification is deliberately independent of model modality, architecture,
training algorithm, accelerator, scheduler, storage engine, inference runtime,
and workflow product. A conformant implementation can specialize those layers
without changing lifecycle semantics.

## 2. Normative language and scope

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**,
**SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **NOT RECOMMENDED**, **MAY**, and
**OPTIONAL** are to be interpreted as described in
[BCP 14](https://www.rfc-editor.org/info/bcp14) when, and only when, they appear
in all capitals.

This specification defines:

- control resources and their identities;
- artifact, event, lineage, and plugin contracts;
- lifecycle behavior from source ingestion through release and operation;
- scale-up, scale-down, federation, and failure invariants;
- on-premises and air-gapped operating requirements;
- security, governance, observability, and conformance requirements.

It does not standardize:

- a model architecture, optimizer, objective, tokenizer, scheduler, kernel, or
  benchmark;
- a single physical storage format for all modalities;
- a universal definition of model quality or safety;
- a claim that orchestration alone produces frontier capability;
- a proprietary system's undisclosed implementation.

## 3. Goals and non-goals

### 3.1 Goals

1. **Clone to value.** One repository and one CLI take a user from code and raw
   data to a locally evaluated model without a hosted account or pre-existing
   platform team.
2. **One lifecycle at every scale.** The same resource schemas, interfaces,
   provenance, and policies apply on one host and across federated clusters.
3. **Any model kind.** Text, image, video, audio, 3D, time-series, graph,
   biological, control-policy, multimodal, and future model types can provide
   adapters without changing the factory core.
4. **Any learning process.** Pretraining, supervised learning, distillation,
   preference optimization, online/offline RL, continual learning, and
   evaluation-only workflows compose from the same resources.
5. **Bring your own code and data.** User code can use any supported language or
   framework; data can be copied, registered in place, streamed, or synchronized
   to storage selected by the user.
6. **Open and replaceable.** No required operation depends on a proprietary
   service, wire protocol, control plane, or hosted account.
7. **On-premises first.** A site can install, operate, update, back up, and
   recover OMF with no external network path.
8. **Frontier-scale execution.** Control and data planes avoid centralized
   per-sample or per-step hot paths and can be partitioned into cells.
9. **Reproducible decisions.** A result can be traced to exact inputs, code,
   configuration, environment, binding, policy, and human or service actor.
10. **Fast, safe iteration.** Mechanical operations and common failures are
   automated; scientific and policy changes remain explicit.

### 3.2 Non-goals

1. OMF does not promise physically unlimited scale. Every installation has
   finite storage, compute, network, coordination, and human capacity.
2. OMF does not require bitwise reproducibility where the selected hardware or
   algorithms cannot provide it.
3. OMF does not require one implementation to optimize every model kind.
4. OMF does not hide infrastructure choices. Bindings are explicit, versioned,
   and included in run provenance.
5. OMF does not treat weights as the complete release product.

### 3.3 North-star user journey

The reference implementation MUST make this journey possible from a clean
checkout:

1. Clone the repository and bootstrap a complete local profile.
2. Validate the host, factory services, and configured credentials.
3. Add or edit user modules without modifying the orchestration core.
4. Import local data, register existing data in place, or connect a stream.
5. Create a versioned `DatasetSnapshot` and inspect its schema, rights, quality,
   and lineage.
6. Configure a user-chosen holding site and synchronize only missing content.
7. Compose model, objective, data mix, trainer, inference, and evaluation
   modules in one `WorkloadSpec`.
8. Run locally, receiving automatic packaging, lineage, streaming,
   checkpointing, recovery, evaluation, metrics, and policy enforcement.
9. Re-run through a cluster binding without changing module or pipeline code.
10. Promote and deploy a passing release through the same repository and
    evidence graph.

The local profile MAY use embedded services and a filesystem, but it MUST use
the same schemas, digests, events, and plugin contracts as distributed profiles.
No step may require a SaaS account.

## 4. Architectural invariants

A conformant implementation SHALL preserve all of these invariants.

### 4.1 Experiments and policies are code

- Scientific workload, data policy, evaluation, deployment, and promotion
  definitions MUST be committed, immutable specifications.
- Every execution MUST have a unique `RunID` and a stable `WorkloadDigest`.
- A generated sweep MUST preserve both the generator revision and each expanded
  workload.
- Manual console actions that alter state MUST emit the same attributable event
  as API actions. Hidden mutation is prohibited.

### 4.2 Assets are immutable revisions

- Data, code, environments, model definitions, checkpoints, evaluations,
  deployments, reviews, policies, and releases are assets.
- An asset revision MUST be immutable. A human-readable name or alias MAY move,
  but the movement MUST be an authorized, auditable event.
- Asset payloads MUST be content-addressed or accompanied by a verified digest.

### 4.3 Lineage is bidirectional

- Every output MUST identify the run, inputs, code, environment, and policy that
  produced it.
- The lineage service MUST support upstream root-cause and downstream impact
  queries.
- Sample-level lineage MUST be available where storage and legal policy permit.
  If lineage is aggregated, the exact granularity and blind spots MUST be
  declared.

### 4.4 Scientific intent is separate from placement

- A `WorkloadSpec` MUST NOT identify a machine, cluster, cloud SKU, rack, or
  accelerator serial number.
- A `Binding` MUST contain operational resource and placement details.
- Rebinding MUST NOT require pipeline source changes.
- Changes that alter scientific semantics—such as global batch size, model
  shape, data policy, precision policy, or objective—belong in the workload, not
  in a binding.

### 4.5 Training and inference share a canonical model package

Training and inference MAY use different optimized implementations. They MUST
consume the same canonical architecture/configuration package and MUST pass its
declared conformance vectors and tolerances. A research result MUST NOT require
a separately maintained production model definition.

### 4.6 Mixtures are streams with history

Dataset mixtures, curricula, and replay policies MUST be immutable revisions.
A live change creates a new revision with an explicit effective boundary; it
never edits a policy in place. Sampler state MUST be checkpointable and
replayable.

### 4.7 Mechanical recovery cannot change meaning

Retry, reschedule, restore, and failover MAY change placement and timing. They
MUST NOT silently change data, code, precision, model topology, objective,
quality gate, or security policy. Any semantic fallback creates a new run.

### 4.8 Open operation

All components required for conformance MUST be available under an OSI-approved
license and MUST operate without a hosted control plane. Artifact-specific
licenses MAY be more restrictive, but they MUST be declared and policy-checked.

### 4.9 Git records intent; stores hold payloads

- The cloned repository is the source of truth for user code, module manifests,
  workloads, connectors, bindings, policies, and deployment intent.
- Data, checkpoints, model packages, and release payloads MUST NOT require Git
  storage. Git contains immutable manifests and references for them.
- Credentials, private keys, and tokens MUST NOT be committed.
- Generated local state and caches MUST be disposable and reconstructable from
  Git, artifact manifests, and durable event/lineage records.
- Moving or replicating a payload between stores MUST NOT change its logical
  asset identity.

## 5. Conceptual architecture

```diagram
                            ┌─────────────────────────────┐
                            │ Git / review / policy input │
                            └──────────────┬──────────────┘
                                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Control and evidence plane                                              │
│ Spec registry · orchestration · event log · asset graph · policy gates │
└───────┬───────────────┬────────────────────┬───────────────┬────────────┘
        │               │                    │               │
        ▼               ▼                    ▼               ▼
┌──────────────┐ ┌──────────────┐   ┌────────────────┐ ┌──────────────┐
│ Data plane   │ │ Train plane  │   │ Inference/RL   │ │ Evaluation   │
│ ingest       │ │ plugins      │   │ execution      │ │ and review   │
│ curate       │ │ checkpoints  │   │ environments   │ │ promotion    │
│ synth/mix    │ │ state sync   │   │ deployments    │ │ release      │
└──────┬───────┘ └──────┬───────┘   └───────┬────────┘ └──────┬───────┘
       └────────────────┴───────────────────┴─────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Execution fabric                                                        │
│ local · Kubernetes · Slurm · other schedulers · federated cells        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ Trust and operations                                                    │
│ identity · secrets · signing · isolation · telemetry · backup · audit  │
└─────────────────────────────────────────────────────────────────────────┘
```

The control plane coordinates coarse lifecycle transitions. Bulk samples,
model tensors, and accelerator collectives MUST flow directly through their
data-plane transports; they MUST NOT transit a central orchestrator.

## 6. Scale model

### 6.1 Cells, not separate products

An OMF **cell** is the smallest independently operable failure and trust domain.
It owns an executor, artifact cache, event outbox, identity authority binding,
and enough metadata to continue admitted work during federation disconnection.

Implementations MAY bind a cell as:

| Binding | Typical physical form | Required semantic behavior |
| --- | --- | --- |
| Local | One process or host; filesystem; embedded metadata | Full schemas, identities, events, lineage, and policy behavior |
| Site | One on-premises cluster; shared object storage and catalog | Distributed scheduling, quotas, failure recovery, HA control services |
| Federation | Multiple cells, sites, or organizations | Signed metadata exchange, policy-aware placement, locality, disconnected progress |

These are deployment bindings, not different OMF editions. An implementation
MUST NOT require users to rewrite a workflow when moving between them.

### 6.2 Scale invariants

1. Control objects and events have bounded size independent of sample count.
2. No globally synchronous database transaction occurs per sample, token,
   environment step, or optimizer step.
3. Data and artifacts are partitionable by immutable identity.
4. Work queues are partitionable by project, workload, or cell.
5. Metadata ingestion supports idempotent at-least-once delivery.
6. Expensive fan-out work is represented as child runs with aggregate lineage,
   not one oversized run record.
7. Global services allocate and reconcile; cells perform local scheduling and
   execution.
8. A disconnected cell can finish or checkpoint already admitted work according
   to policy and later reconcile signed events.

### 6.3 Scientific portability

The same `WorkloadSpec` MUST be executable with different compatible bindings.
The resulting `RunID`, binding provenance, timing, and potentially numerical
result will differ.

If a workload cannot fit on a smaller target, a user MAY:

- select a compatible offload/sharding binding; or
- create an explicit shape-reduced workload from the same schema and graph.

Calling a shape-reduced workload “the same run” is prohibited. The portability
claim concerns interfaces and graph semantics, not suspension of physical
memory and compute limits.

### 6.4 Measured capacity

Every installation MUST publish a dated `CapacityReport` containing:

- tested concurrent runs and environment sessions;
- tested accelerator and CPU worker counts;
- metadata/event throughput and retention;
- artifact and sample throughput;
- scheduler placement latency distribution;
- checkpoint commit/restore throughput;
- failure modes encountered and recovery results;
- the exact software, hardware, network, and test workload revisions.

“Frontier scale” conformance requires measured operation at 1,024 or more
accelerators in one coordinated workload or federation. It does not imply model
quality.

## 7. Common object and wire model

### 7.1 Serialization and schema

- Control resources MUST have versioned JSON Schemas.
- YAML MAY be accepted as authoring syntax but MUST be converted to the
  canonical JSON data model before validation and hashing.
- Canonical JSON used in digests MUST follow
  [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785).
- Control values MUST use explicit units. NaN, infinity, ambiguous local time,
  and implicit environment-variable interpolation are prohibited.
- Breaking schema changes require a new API version and a deterministic
  migration tool.

Every resource contains at least:

```yaml
apiVersion: omf.dev/v1alpha1
kind: <Kind>
metadata:
  name: <human-readable-name>
  namespace: <trust-domain/project>
  uid: <globally-unique-id>
  revision: <immutable-revision>
  createdAt: <RFC3339-UTC>
  createdBy: <workload-or-human-identity>
spec: {}
status: {}
```

`spec` is desired immutable content. `status` is observed state and MUST NOT be
included in the `specDigest`.

### 7.2 Identifiers

- `RunID` MUST be a UUIDv7.
- Immutable content MUST carry a digest in `algorithm:value` form. SHA-256 is
  the minimum REQUIRED algorithm; implementations MUST allow future algorithms.
- A `WorkloadDigest` is the digest of canonical workload content and referenced
  immutable specifications.
- A `BindingDigest` is computed independently.
- A logical asset URI SHOULD use
  `omf://<trust-domain>/<namespace>/<kind>/<name>@<revision>`.
- Payload identity MUST rely on its digest, not a mutable URI.

The `RunID` distinguishes attempts. The workload and binding digests establish
what each attempt meant.

### 7.3 Events

Lifecycle events MUST use a
[CloudEvents](https://cloudevents.io/) 1.0-compatible envelope. Required OMF
event fields are:

- event ID, type, source, subject, and UTC time;
- resource UID and immutable revision;
- run ID where applicable;
- actor workload identity;
- workload, binding, and policy digests where applicable;
- monotonic resource sequence number;
- payload schema URI and payload digest;
- signature or authenticated transport evidence.

Core event types include:

- `SpecValidated`, `RunAdmitted`, `RunStateChanged`;
- `ArtifactCommitted`, `CheckpointCommitted`;
- `SamplerPolicyActivated`, `PolicyStatePublished`;
- `EvaluationCompleted`, `ReviewRecorded`;
- `PolicyDecisionRecorded`, `AliasMoved`;
- `DeploymentChanged`, `ReleasePublished`;
- `ArtifactRevoked`, `LineageReconciled`.

Consumers MUST be idempotent by event ID. Producers MUST use an outbox or an
equivalent atomic publication mechanism so resource state and its event cannot
silently diverge.

### 7.4 Artifacts

An `Artifact` manifest MUST include:

- media type, byte size, digest, and one or more retrievable locations;
- logical kind and schema revision;
- producing run and direct input revisions;
- creation identity and time;
- license, source-rights, use restrictions, and attribution references;
- sensitivity, retention, residency, and export classification;
- signatures and attestations;
- encryption and key reference metadata, without key material;
- chunk/index information for large payloads.

OMF artifacts SHOULD use OCI manifests and descriptors for portable packaging.
Very large datasets and checkpoints MAY keep payload shards in object storage
while an immutable OCI artifact contains or references their content-addressed
index. A mutable object-store prefix is not an artifact identity.

### 7.5 Core resource kinds

| Kind | Purpose |
| --- | --- |
| `Project` | Namespace, ownership, budget, policy, and trust boundary |
| `Module` | Versioned user-code entry point with typed contracts and capabilities |
| `Artifact` | Immutable payload or content-addressed collection |
| `ArtifactStore` | User-selected physical holding site and its capabilities |
| `DataConnector` | Versioned adapter to discover, snapshot, read, or write data |
| `SyncPlan` | Direction, selection, destination, verification, and retention for replication |
| `DatasetSnapshot` | Typed sample collection, rights, statistics, and partitions |
| `MixSpec` | Versioned source weights, curriculum, replay, and exhaustion rules |
| `SamplerState` | Exact stream position and deterministic sampling state |
| `GenerationSpec` | Synthetic or transformed data graph and validation policy |
| `ModelPackage` | Canonical model definition, signatures, adapters, and tests |
| `EnvironmentSpec` | Reproducible interactive or batch world plus verifier |
| `WorkloadSpec` | Scientific graph and immutable semantic parameters |
| `Binding` | Executor, resources, placement, transport, and operational tuning |
| `Run` | One attempt of a workload with a binding |
| `Checkpoint` | Atomic executable model and optimizer/process state |
| `EvaluationSpec` | Versioned protocol, data, environment, metrics, and inference policy |
| `EvaluationResult` | Scores, uncertainty, traces, failures, and provenance |
| `DeploymentSpec` | Model release, runtime, objective, routing, and scaling policy |
| `FeedbackSpec` | Governed capture and conversion of operational signals into candidate assets |
| `Review` | Attributable human or agent qualitative assessment |
| `PolicyDecision` | Allow/deny/warn outcome with evidence and policy digest |
| `Release` | Signed publication manifest over all releasable evidence |
| `CapacityReport` | Reproducible scale and failure benchmark |

## 8. Model- and modality-neutral contracts

### 8.1 Typed sample envelope

A `SampleEnvelope` contains:

- globally unique sample identity and source revision;
- one or more named `Part` values;
- relationships among parts, such as input, target, context, observation, or
  annotation;
- provenance and rights references;
- split, group, quality, and safety labels;
- deterministic transform history.

A `Part` is one of:

- inline scalar or structured value;
- typed tensor with shape, dtype, axes, and semantics;
- ordered or timestamped sequence;
- media/object reference with media type and digest;
- graph or relation set with a schema URI;
- stream reference with framing and ordering rules.

The factory core MUST NOT assume tokens, messages, images, fixed tensors, or an
OpenAI-style chat schema. Modality plugins define additional schemas and
validators.

### 8.2 Model package

A `ModelPackage` MUST contain or reference:

1. architecture and parameter schema;
2. input, output, state, and batching signatures;
3. preprocessing and postprocessing definitions;
4. initialization and checkpoint mapping rules;
5. training reference adapter;
6. one or more OPTIONAL optimized training/inference adapters;
7. deterministic conformance vectors and numerical tolerances;
8. supported precision, parallelism, device, and export capabilities;
9. code, dependency, license, security, and build provenance;
10. migration rules for compatible package revisions.

The canonical package MAY contain executable code because frontier
architectures often precede portable graph formats. Such code MUST be signed,
scanned, sandboxed during validation, and policy-approved before execution.

### 8.3 Capability negotiation

Every plugin MUST publish a signed capability manifest including:

- contract and plugin versions;
- supported model/sample/environment schemas;
- devices, precisions, parallel strategies, and transports;
- deterministic and elastic behavior;
- resource prerequisites and incompatibilities;
- security/isolation assumptions;
- checkpoint and migration formats.

Admission MUST fail before resource allocation when required capabilities do not
match. Silent feature downgrade is prohibited.

### 8.4 Cloneable repository contract

The reference distribution MUST be usable as both an open factory distribution
and a project workspace. A clean checkout contains or creates this logical
layout:

```text
omf.yaml
factory/
modules/
connectors/
data/
workloads/
bindings/
policies/
deployments/
.omf/
```

- `omf.yaml` identifies the project, schema revision, defaults, and enabled
  modules. It MUST NOT contain credentials.
- `factory/` contains the open implementation and SDK or a reproducible lock to
  them.
- `modules/` and any module-referenced paths contain user code.
- `connectors/`, `data/`, `workloads/`, `bindings/`, `policies/`, and
  `deployments/` contain versioned desired-state manifests.
- `.omf/` contains only recreatable local databases, caches, sockets, logs, and
  runtime state and MUST be ignored by Git.
- Raw data and secret files MUST be ignored by default. A user may explicitly
  version a small non-sensitive fixture.

The repository MUST remain relocatable: cloning it into a different absolute
path cannot change resource identity or require manifest edits. A clean clone
plus authorized access to declared stores MUST reconstruct every retained
non-secret project asset and its lineage.

### 8.5 User module contract

A `Module` allows a user to write all scientific and domain code without adding
special cases to the factory core. Module kinds include model, objective,
transform, generator, sampler, trainer, inference runtime, environment,
evaluator, policy, connector, and deployment adapter.

Every module manifest MUST declare:

- module kind, contract version, name, code root, and entry point;
- input, output, configuration, and state schemas;
- dependency lock or immutable execution environment;
- capabilities, resource requirements, and supported platforms;
- determinism, checkpoint, concurrency, and side-effect behavior;
- required secrets and network destinations by symbolic reference;
- license and source/build provenance;
- unit fixtures and contract conformance tests.

Modules compose through typed artifacts and lifecycle contracts, not imports
into orchestration internals. At run admission, OMF MUST package the exact
committed code and dirty-worktree policy, resolve dependencies, compute the
module digest, and attach it to lineage. A run with uncommitted code is allowed
only by explicit policy and MUST archive and digest the exact patch/content.

### 8.6 Reference CLI and API

The reference implementation MUST expose one `omf` CLI with these stable
operations and equivalent machine APIs:

| Operation | Required outcome |
| --- | --- |
| `omf bootstrap` | Create or reconcile a complete selected factory profile |
| `omf doctor` | Validate host, services, devices, stores, identity, and policy |
| `omf module validate/test` | Validate and execute module contract tests |
| `omf data add` | Copy, register, or stream data into an immutable snapshot |
| `omf store add` | Declare a holding site without writing credentials to Git |
| `omf sync push/pull` | Plan, transfer, verify, and record artifact replicas |
| `omf run` | Validate, package, synchronize prerequisites, admit, and execute a workload |
| `omf lineage` | Query upstream derivation and downstream impact |
| `omf evaluate` | Run or inspect a versioned evaluation suite |
| `omf deploy` | Policy-check, promote, deploy, observe, and roll back a release |

Mutating commands MUST support a dry-run/plan mode. Long-running commands MUST
return a durable operation or run ID and support detach/reattach. All commands
MUST support structured output suitable for automation. CLI behavior MUST be a
client of the same authenticated API used by other interfaces; it cannot bypass
events, lineage, or policy.

## 9. Workload and execution contracts

### 9.1 Workload specification

A `WorkloadSpec` declares:

- input asset revisions and output contracts;
- model package and objective plugins;
- ordered or graph-structured stages;
- semantic parameters, random-seed policy, and sample budget;
- checkpoint and evaluation triggers;
- acceptable reproducibility class;
- data, safety, budget, and release policies;
- declared child-work generation, such as sweeps or rollout batches.

It does not declare physical hostnames, queues, racks, or vendor SKUs.

### 9.2 Deployment binding

A `Binding` declares:

- executor and scheduler adapter;
- resource capabilities and quantities;
- topology, locality, queue, quota, and preemption class;
- storage, cache, sample, checkpoint, and telemetry transports;
- distributed parallel plan and operational microbatching;
- retry, timeout, checkpoint cadence, and recovery policy;
- identity, network, isolation, and secret profiles.

A binding MUST identify every implementation and image by immutable revision.
Operational microbatching MAY differ if the workload's declared global batch and
numerical semantics remain satisfied.

### 9.3 Run state machine

```diagram
┌───────┐   ┌─────────┐   ┌──────────┐   ┌─────────┐   ┌───────────┐
│ Draft │──▶│Validated│──▶│ Admitted │──▶│ Running │──▶│ Succeeded │
└───────┘   └────┬────┘   └────┬─────┘   └───┬─┬───┘   └───────────┘
                 │             │             │ │
                 ▼             ▼             │ └──────▶ Failed
               Rejected      Canceled        │
                                             ▼
                                        Recovering
                                             │
                                             └────────▶ Running
```

- Transitions MUST be compare-and-set or otherwise protected from lost updates.
- Child runs MUST carry the parent run ID and purpose.
- `Recovering` MUST identify the checkpoint and failed resources.
- Cancellation MUST define whether the implementation commits a final
  checkpoint, discards partial output, or times out.
- A terminal run MUST have a reason, final event sequence, and output list.

### 9.4 Scheduling

The scheduler contract MUST support:

- capability and topology-aware placement;
- gang/all-or-nothing admission for tightly coupled workloads;
- queueing, quotas, fair sharing, reservations, and priorities;
- whole-job preemption and backfill;
- fixed and elastic workloads with explicit minimum/maximum resources;
- data and checkpoint locality hints;
- node health qualification and quarantine;
- owned, leased, or federated capacity without changing the workload.

The scheduler decides **where and when**, not **what the scientific workload
means**. OMF SHOULD bind existing schedulers rather than inventing one.

## 10. Data, synthesis, and streaming mixtures

### 10.1 Dataset snapshots

A `DatasetSnapshot` MUST describe:

- sample and modality schemas;
- immutable partition/chunk identities;
- source, transformation, and synthetic provenance;
- licensing, consent, privacy, safety, and intended-use constraints;
- statistics, quality reports, rejected-sample reasons, and known limitations;
- deduplication domain and contamination checks;
- loader capabilities and locality.

Croissant metadata SHOULD be emitted where its vocabulary can represent the
dataset. Domain-specific extensions MAY coexist with it.

### 10.2 Transformations and curation

- Each transform is a versioned activity with immutable inputs and outputs.
- Rejected records SHOULD remain represented by non-sensitive decision metadata
  so filter behavior can be audited.
- Dataset branches MUST retain a common ancestor.
- A transformation MUST declare deterministic behavior and seed use.
- Quality signals produced by learned models MUST identify those model revisions.
- A deletion or rights revocation MUST trigger a downstream impact query and a
  policy-controlled rebuild, quarantine, or documented exception.

### 10.3 Synthetic generation

A `GenerationSpec` MUST declare:

- seeds, prompts/instructions, environments, generators, and model revisions;
- orchestration graph, stopping rules, and budgets;
- validators, judges, filters, thresholds, and human review requirements;
- output schema and destination dataset;
- randomness and replay policy;
- source-rights inheritance and synthetic-data labels.

Generator, judge, and validator are role interfaces; any may be a model,
simulator, deterministic program, human queue, or composition. Synthetic origin
MUST NOT erase upstream license, privacy, or provenance obligations.

### 10.4 Mix specification

A `MixSpec` MUST contain:

- immutable source snapshot revisions;
- source weights or a versioned weight schedule;
- sampling with/without replacement;
- oversampling, exhaustion, and live-source behavior;
- grouping, ordering, curriculum, and quality constraints;
- global seed and named counter-based RNG algorithm;
- sharding and consistency policy;
- amendment authorization policy;
- optional optimization objective and experiment evidence.

Weights MUST have explicit normalization semantics. The mix service MUST expose
the effective distribution and observed delivery distribution.

### 10.5 Globally coherent sampling and replay

For a given mix revision and state, all workers participate in one logical
sample stream. Independent per-worker shuffles that change the global mixture
are non-conformant unless explicitly requested by the workload.

A `SamplerState` MUST record:

- active mix revision and amendment history;
- global logical sample index or equivalent cursor;
- RNG algorithm/state;
- source cursors and epochs;
- assignment leases or rank mapping;
- delivery guarantee (`exact`, `at-least-once`, or declared approximate);
- world size and redistribution history;
- acknowledged and outstanding ranges.

The preferred design maps deterministic global ranges to temporary workers so
world-size changes do not redefine the logical stream. If an implementation
cannot preserve this property, it MUST record the divergence and cannot claim
exact replay.

### 10.6 Mid-run amendments

A mid-run mixture change:

1. creates a new immutable `MixSpec` revision;
2. passes authorization and compatibility validation;
3. declares an effective global sample index or checkpoint boundary;
4. emits `SamplerPolicyActivated`;
5. is acknowledged by every active sampler partition;
6. is included in the next checkpoint and lineage graph.

Workers MUST NOT apply an uncoordinated in-place weight update. Reproduction
replays the amendment timeline, not merely the final weights.

### 10.7 Data connector and holding-site contract

Users choose where original data lives and where OMF-managed replicas are held.
The core MUST NOT require one cloud, object-store API, table format, or storage
vendor. Filesystems, NAS, object stores, lakehouses, databases, streams, and
domain repositories are supported through `DataConnector` capabilities.

An `ArtifactStore` declares:

- driver and immutable driver revision;
- endpoint or mount by non-secret reference;
- read, write, list, range-read, multipart, watch, transaction, retention,
  versioning, and server-side-copy capabilities;
- locality, residency, sensitivity, quota, and cost labels;
- digest and consistency guarantees;
- encryption and credential references;
- whether it is authoritative, a replica, a cache, or export-only.

A `DataConnector` MUST implement the applicable subset of:

- `discover(selector)` to enumerate candidate source objects;
- `inspect(source)` to obtain schema, rights, size, and version metadata;
- `snapshot(source, policy)` to establish an immutable source boundary;
- `read(snapshot, ranges)` and OPTIONAL `stream(snapshot, cursor)`;
- `write(artifact, destination)` for holding-site adapters;
- `verify(artifact, location)` using OMF content digests;
- `watch(source, cursor)` for explicitly configured append/live sources.

Connector credentials are secret references resolved at execution. A connector
MUST NOT place credentials or signed temporary URLs in Git, events, lineage, or
logs. Provider ETags MUST NOT be treated as cryptographic content identity
unless the provider's exact digest semantics are known and recorded.

### 10.8 Bring, register, stream, and synchronize data

`omf data add` or its API equivalent supports four explicit modes:

| Mode | Semantics |
| --- | --- |
| `copy` | Read source data into the local content-addressed store and create a snapshot |
| `register` | Leave bytes in place, hash/inspect them, and record an immutable external location |
| `mount` | Register a binding-provided read-only path while retaining content identity |
| `stream` | Register an ordered source and cursor/version policy for bounded or live consumption |

If an external system cannot pin an immutable version, OMF MUST hash the
observed content and verify it before each reproducible use. A mutable reference
without drift detection cannot satisfy `operational`, `numerical`, or `bitwise`
reproducibility.

A `SyncPlan` declares source and destination stores, selected artifact
revisions, push/pull/mirror direction, concurrency, bandwidth limits,
encryption, verification, cache, retention, and failure policy. Synchronization
MUST:

1. resolve immutable manifests and authorize source, destination, and purpose;
2. compare content digests and plan only missing chunks;
3. transfer through a resumable, bounded-concurrency data path;
4. verify bytes at the destination independently of transport success;
5. atomically publish a replica location only after all required chunks pass;
6. emit lineage/audit events without exposing secret locations;
7. leave the source and unrelated destination content unchanged by default.

Deletion and garbage collection are separate, policy-controlled operations;
`sync` MUST NOT imply deletion. Interrupted transfers are resumable and MUST
NOT appear as committed replicas. The same mechanism applies to datasets,
checkpoints, model packages, environments, and releases.

A binding MAY require particular stores for locality or durability. `omf run`
MUST show the prerequisite sync plan during dry-run and may automatically
execute an approved plan before admission. It MUST NOT send data to an
undeclared destination or cross a residency/trust boundary silently.

## 11. Training and checkpoints

### 11.1 Trainer plugin

A trainer adapter MUST implement these lifecycle operations or their equivalent:

- `validate(model, objective, workload, binding)`;
- `prepare(inputs, model, samplerState)`;
- `run()` with progress and health events;
- `quiesce(reason)` at a consistency boundary;
- `checkpoint(reason)`;
- `restore(checkpoint)`;
- `publishPolicyState(targets)` for OPTIONAL online learners;
- `stop(mode)`.

It MUST report the realized parallel plan, global batch semantics, precision,
kernel/library revisions, random state, and accepted sample ranges.

### 11.2 Distributed execution

The training contract permits data, fully sharded data, tensor, pipeline,
expert, sequence, context, and future parallelisms. A binding composes them.

- Rank and topology discovery MUST come from the executor, not hard-coded
  addresses.
- Collective timeouts and failures MUST be observable.
- Implementations SHOULD hash or otherwise verify replicated state periodically.
- Elastic world-size changes MUST occur at an adapter-declared safe boundary.
- A backend that cannot safely resize MUST checkpoint and restart; it MUST NOT
  claim in-place elasticity.

### 11.3 Atomic checkpoint protocol

Checkpoint publication follows this minimum protocol:

1. Quiesce or capture a declared consistent state.
2. Write all content-addressed shards to staging.
3. Verify shard digests and completeness.
4. Write an immutable checkpoint manifest containing model, optimizer,
   scheduler, RNG, sampler, workload, binding, code, environment, and parent
   checkpoint references.
5. Atomically publish the manifest or compare-and-set its committed status.
6. Emit `CheckpointCommitted` only after commit.

Readers MUST ignore uncommitted shards and manifests. Recovery MUST select a
committed checkpoint by policy, never by filename ordering alone.

### 11.4 Portability

A checkpoint MAY contain:

- a canonical logical state suitable for conversion;
- backend-optimized sharded state for fast local recovery; or
- both.

Its manifest MUST declare portability constraints. A release candidate MUST
either provide a canonical serving state or a signed, tested conversion run.
Cross-backend conversion produces a new artifact with derivation lineage; it
does not mutate the source checkpoint.

## 12. Interactive environments and reinforcement learning

### 12.1 Environment contract

An `EnvironmentSpec` packages a reproducible world: code repository, simulator,
robot scene, game, browser, scientific instrument model, database, media editor,
or another domain.

An environment adapter MUST support:

- `create(specRevision, taskRevision, seed, limits)`;
- `observe(session)`;
- `step(session, typedAction, idempotencyKey)`;
- `snapshot(session)` when declared supported;
- `evaluate(session, verifierRevision)`;
- `close(session)`.

Responses include typed observations, costs, termination status, timestamps,
and provenance. The action/observation schema is environment-defined, not
text-only.

### 12.2 Isolation

Model-generated actions are untrusted. Environment workers MUST have:

- isolation appropriate to the threat model, stronger than a plain container
  for arbitrary code unless a documented risk acceptance says otherwise;
- per-session CPU, memory, accelerator, storage, process, time, and syscall
  limits;
- deny-by-default network egress and explicit destination policy;
- ephemeral writable state and immutable mounted inputs;
- no control-plane credentials or artifact-store write credentials;
- complete security and resource audit events.

Verifiers and hidden tests MUST be isolated from the acting model. Training and
evaluation environments MAY reuse the same interface and base artifact, but
holdout secrets and reward logic MUST remain separate.

### 12.3 RL roles

OMF models online and offline RL as replaceable roles:

- task source;
- actor/inference pool;
- environment pool;
- reward/verifier service;
- trajectory store or stream;
- learner/trainer;
- policy-state publisher;
- evaluator.

The algorithm is not part of the operations contract. A workload MUST declare
on/off-policy assumptions, accepted policy staleness, trajectory rejection
rules, reward composition, and synchronization boundary.

### 12.4 Policy-state publication

Committed checkpoints are the universal synchronization mechanism. An OPTIONAL
fast transport may transfer weights or deltas directly between trainer and
actor devices.

Fast publication MUST still create a `PolicyState` identity containing source
checkpoint or trainer step, tensor mapping, precision/conversion, recipient
generation, integrity verification, and activation boundary. Actors MUST not
combine incompatible generations within one trajectory unless the algorithm
explicitly allows and records it.

## 13. Inference and deployment

### 13.1 Inference contract

The common inference contract accepts:

- model package and state revisions;
- named method such as `predict`, `generate`, `embed`, `score`, or a
  model-defined method;
- typed input parts and optional session state;
- deterministic seed where supported;
- runtime parameters, deadline, priority, and trace context;
- requested output schema and streaming mode.

It returns typed output parts, model/policy-state identity, realized runtime
parameters, token/sample/tensor accounting as appropriate, finish status, and
trace identity. Chat-completion compatibility MAY be an adapter; it is not the
core protocol.

### 13.2 Train/inference conformance

For every model/runtime pair:

1. Execute package conformance vectors against the training reference.
2. Execute the optimized runtime with the same model state and inputs.
3. Compare named intermediates and outputs using package-declared dtype-aware
   tolerances.
4. Test supported batch, sequence/shape, state, and device boundaries.
5. Persist the conformance result as a release-gate asset.

Optimization, quantization, compilation, and export each produce derived
artifacts and require new conformance evidence.

### 13.3 Deployment

A `DeploymentSpec` declares:

- immutable model release and runtime adapter;
- target form, such as online service, batch job, actor pool, embedded package,
  edge device, or domain-specific control runtime;
- fixed or elastic replica limits;
- hardware capability and locality policy;
- latency, throughput, cost, energy, and quality objectives;
- batching, caching, session, and routing policy;
- rollout strategy, health checks, and rollback criteria;
- network, identity, tenancy, logging, and retention policy.

Evaluation, synthetic generation, RL acting, batch inference, human review, and
online serving SHOULD use the same inference contract. They MAY use different
bindings optimized for their objectives.

### 13.4 Operational feedback and continual iteration

Production inputs, outputs, labels, user feedback, failures, drift signals, and
operator interventions are sensitive, untrusted source data. They MUST NOT
become training input merely because they were logged.

A `FeedbackSpec` declares:

- source deployment and release revisions;
- allowed fields, collection purpose, consent or other rights basis;
- sampling, redaction/de-identification, retention, and residency;
- quality, abuse, poisoning, privacy, and safety filters;
- annotation/review protocol and output `DatasetSnapshot` schema;
- policy gates required before evaluation or training use.

Accepted feedback is materialized as a new immutable dataset with full lineage.
It enters training only through a newly committed workload and mix revision.
Drift or regression MAY trigger evaluation, rollback, or a proposed workload;
it MUST NOT silently update deployed weights. Continual-learning systems still
publish explicit policy-state, checkpoint, evaluation, promotion, and
deployment revisions.

## 14. Evaluation, review, promotion, and release

### 14.1 Evaluation specification

An `EvaluationSpec` MUST identify:

- model state and inference configuration;
- immutable dataset/task and environment revisions;
- prompt/template/preprocessing and tool schemas where applicable;
- metric and verifier implementations;
- seeds, repeats, decoding/sampling policy, and resource limits;
- contamination, leakage, and benchmark-hacking controls;
- aggregation, uncertainty, slice, and failure-reporting rules;
- pass/fail thresholds or decision policy, if used.

Evaluation is independently schedulable. `CheckpointCommitted` MAY trigger a
suite, but evaluation failure MUST NOT corrupt or block checkpoint commit.

### 14.2 Evaluation integrity

- Reimplementations of external benchmarks MUST record upstream revision,
  modifications, and differential validation against the upstream harness.
- Hidden holdouts and verifiers MUST be access-controlled separately from
  training data and actors.
- Results MUST include distributions, uncertainty, invalid samples, timeouts,
  resource usage, and protocol failures—not only a mean score.
- Training and release gates SHOULD use multiple capability and risk signals.
- A metric used directly as a reward MUST be treated as compromised for
  independent evaluation unless a documented separation exists.

### 14.3 Human and agent review

A `Review` contains reviewer identity and type, model revision, interface and
session configuration, structured findings, referenced traces, conflicts of
interest, and timestamp. Agent-generated reviews identify the reviewing model
and are not equivalent to independent human approval.

Sensitive review content MUST follow project retention and access policy.

### 14.4 Promotion

Promotion is a policy decision over immutable evidence, followed by an alias or
deployment change. It is not artifact copying or model mutation.

Policy gates MAY cover:

- capability and regression thresholds;
- safety, privacy, security, fairness, and misuse evaluations;
- data rights, licenses, attribution, and export controls;
- train/inference conformance;
- reproducibility and lineage completeness;
- cost, latency, throughput, energy, and capacity;
- required human approvals and separation of duties.

Every decision MUST include policy revision, evidence revisions, decision,
reason, actor, and expiration or re-evaluation condition.

### 14.5 Release

A release is one signed manifest containing or referencing:

- canonical model package and model state;
- preprocessing, postprocessing, and runtime requirements;
- workload and binding provenance;
- data and synthetic-generation summaries with rights;
- evaluation results and known limitations;
- risk record, intended use, prohibited use, and mitigations;
- conformance vectors/results;
- software and AI bill of materials;
- signatures, build/source attestations, and vulnerability status;
- deployment examples and rollback compatibility;
- license and attribution files.

Weights alone do not constitute an OMF release.

## 15. Provenance and reproducibility

### 15.1 Provenance model

OMF lineage follows the W3C PROV concepts of entity, activity, and agent:

- resource/artifact revisions are entities;
- runs and policy actions are activities;
- users, services, organizations, and models acting autonomously are agents.

Implementations SHOULD emit OpenLineage-compatible job, run, and dataset events
with versioned OMF custom facets. OMF resources not naturally represented as
datasets remain first-class entities in the OMF graph.

### 15.2 Required lineage questions

A conformant lineage service MUST answer:

1. Which source samples, transforms, mixtures, code, environments, and actors
   contributed to this checkpoint or release?
2. Which checkpoints, evaluations, deployments, and releases depend on this
   source or component revision?
3. Which sampler policy and exact sample ranges preceded a reported anomaly?
4. Which runtime and conversion produced this deployed model?
5. Which policy and evidence authorized this release or alias movement?
6. What must be quarantined or rebuilt after a source-rights revocation,
   vulnerability, poisoned sample, or faulty component is discovered?

### 15.3 Reproducibility classes

Every workload declares and every result reports one class:

| Class | Guarantee |
| --- | --- |
| `lineage` | Inputs, code, configuration, binding, and events can be reconstructed |
| `operational` | The workflow can be re-executed with compatible components |
| `numerical` | Declared metrics/outputs repeat within stated tolerance and confidence |
| `bitwise` | Declared state and outputs are byte-identical on the qualified platform |

Higher classes include lower ones. Implementations MUST NOT claim `bitwise`
solely because configuration files are versioned.

### 15.4 Retention and erasure

Immutability does not override privacy or legal deletion duties. An installation
MUST support tombstone/revocation events, access denial, impact analysis, and
payload deletion or cryptographic erasure. Historical metadata SHOULD retain a
non-sensitive record that an artifact existed and was revoked, where lawful.

## 16. Security, trust, and governance

### 16.1 Threat model

At minimum, each installation MUST address:

- poisoned, mislabeled, unlicensed, or privacy-sensitive data;
- malicious model packages, plugins, dependencies, and build inputs;
- arbitrary model-generated code and network actions;
- artifact tampering, rollback, and substitution;
- checkpoint/model theft and training-data exfiltration;
- compromised workers, cells, users, and federation peers;
- reward hacking, evaluation leakage, and benchmark contamination;
- secrets exposed through logs, prompts, traces, or checkpoints;
- denial of service and quota abuse;
- unsafe or unauthorized model promotion and deployment.

### 16.2 Identity and authorization

- Humans and workloads MUST have distinct, short-lived, attributable identities.
- Workload identity SHOULD follow SPIFFE-compatible semantics.
- Service-to-service traffic crossing a trust boundary MUST be mutually
  authenticated and encrypted.
- Authorization MUST consider actor, project, resource, action, purpose,
  sensitivity, residency, and policy revision.
- Long-lived shared credentials in workload images are prohibited.
- Federation MUST use explicit trust bundles and least-privilege delegation.

### 16.3 Artifact and software supply chain

- Executable artifacts and releases MUST be signed.
- Admission MUST verify digest, signature, source/build provenance, policy, and
  known-vulnerability status.
- Builds SHOULD emit SLSA-compatible provenance and SPDX or CycloneDX bills of
  materials.
- Model and dataset derivations require AI-specific lineage in addition to
  software build provenance; SLSA alone is insufficient.
- Imported offline bundles MUST be verified before entering the trusted
  registry.

### 16.4 Secrets and sensitive data

- Secrets MUST come from an authenticated secret service and be scoped to one
  workload purpose.
- Secret values MUST NOT enter specs, images, lineage, telemetry, or checkpoints.
- Data and model state MUST be encrypted in transit and at rest according to
  project classification.
- Debug and human-review surfaces MUST enforce the same data policy as storage.

### 16.5 Policy and risk governance

Policy evaluation MUST be local, versioned, deterministic where possible, and
available in an air gap. NIST AI RMF's Govern, Map, Measure, and Manage functions
are an informative structure for risk records, not an automatic compliance
claim.

High-impact promotion policies SHOULD support separation of duties, expiring
approvals, and emergency revocation. Policy bypasses MUST be time-bound, signed,
visible, and included in release evidence.

## 17. On-premises and air-gapped operation

### 17.1 Required site services

An OMF site binding declares implementations for:

- source control and review;
- specification validation and CI;
- OCI registry and artifact/object storage;
- metadata, event, and lineage storage;
- workflow orchestration and workload execution;
- scheduler and resource inventory;
- identity, PKI, secrets, and policy;
- telemetry collection, storage, and visualization;
- DNS, trusted time, backup, and disaster recovery.

Each role MAY be embedded on one host or highly available across many nodes.
The APIs and resource semantics do not change.

### 17.2 Offline installation bundle

A release of an OMF implementation MUST be exportable as a signed offline
bundle containing:

- exact images, packages, charts/manifests, schemas, and migration tools;
- dependency lockfiles and bills of materials;
- signatures, transparency evidence or offline trust roots;
- hardware/OS prerequisites and capacity calculator;
- install, upgrade, rollback, backup, restore, and break-glass procedures;
- conformance suite and expected results;
- locally renderable documentation.

Installation and normal operation MUST succeed with denied internet egress.
Telemetry MUST remain local by default. Any update or vulnerability feed import
is an explicit, signed transfer.

### 17.3 Operations

- Control-plane services MUST define backup consistency, RPO, and RTO.
- Artifact backup MUST preserve manifests, payload digests, signatures, and key
  recovery procedures.
- Schema and storage migrations MUST be resumable, observable, and reversible
  until commit.
- A site MUST be able to rebuild derived indexes from the event log and
  immutable manifests.
- Key rotation, certificate expiry, clock failure, and disconnected operation
  MUST be tested.
- No license check, feature flag, or identity dependency may call an external
  vendor for required operation.

### 17.4 Clone and bootstrap contract

From the repository root, `omf bootstrap --profile local` MUST create a usable
single-host factory without requiring Kubernetes, Slurm, an external database,
or a remote artifact store. The local profile provides repository-scoped
implementations of artifact storage, metadata, events, lineage, identity,
secrets, execution, and telemetry.

Bootstrap MUST be:

- idempotent and resumable;
- able to produce a complete plan before it mutates the host;
- scoped to the clone and `.omf/` unless the user approves a system change;
- explicit about downloads, ports, services, disk use, and device access;
- compatible with a verified offline installation bundle;
- non-destructive to user modules and manifests during upgrade or rollback.

`omf doctor` MUST verify repository schema, dependency locks, filesystem and
device capabilities, store connectivity, identity, secrets, time, network
policy, capacity, and recovery prerequisites. It MUST give actionable local
remediation and structured output.

Site and federation bootstrap use the same project manifests with different
bindings. They MAY install external open services, but MUST present and persist
the complete plan and immutable component revisions. No bootstrap path may
silently create a hosted account, upload project metadata, or enable telemetry
egress.

## 18. Federation

### 18.1 Federation control

A federation broker exchanges resource offers, workload requirements, policy
labels, and signed summaries. It SHOULD place work near data and compatible
hardware. It MUST NOT require raw sensitive metadata or payloads when a policy
label or attestation suffices.

Cells retain authority over local admission and MAY reject globally proposed
work. Global cancellation, revocation, and policy updates reconcile through
ordered signed events.

### 18.2 Data and artifact movement

- Movement MUST satisfy residency, license, privacy, export, and trust policy.
- Transfer uses content identity, chunk verification, encryption, and resumable
  transport.
- Caches are untrusted until digest and signature verification.
- Compute-to-data placement is preferred when payload movement is prohibited or
  inefficient.
- Federated learning or secure aggregation MAY be plugins; they are not assumed
  by the base architecture.

### 18.3 Disconnection and reconciliation

During disconnection, a cell may continue already admitted work within its
lease and policy. It records events in a signed ordered outbox. On reconnect,
the federation verifies identity, sequence, signatures, and policy epoch before
merging. Conflicting mutable aliases require explicit resolution; immutable
artifacts do not conflict.

## 19. Observability, accounting, and SLOs

### 19.1 Signals

Components MUST emit vendor-neutral traces, metrics, and structured logs through
OpenTelemetry-compatible instrumentation or a conforming bridge. Trace context
SHOULD propagate from run admission through data access, training, inference,
environment steps, evaluation, and release.

Telemetry is distinct from provenance:

- telemetry explains transient behavior and performance;
- provenance explains identity, derivation, and decisions.

Neither substitutes for the other.

### 19.2 Required measures

Each installation records, where meaningful:

- configuration-to-validation, admission, start, first-signal, checkpoint, and
  evaluation latency;
- queue time, placement distribution, preemption, and backfill;
- accepted samples/bytes/steps per wall-clock time;
- accelerator, CPU, memory, network, and storage goodput/utilization;
- checkpoint commit and restore duration;
- automatic recovery success, lost work, and human escalation;
- requested versus observed mix distribution and replay divergence;
- inference latency/throughput/quality by deployment objective;
- environment startup, step, failure, and verifier rates;
- model quality, uncertainty, slice regressions, and invalid-result rates;
- energy, resource-hours, and attributable financial cost when available;
- lineage completeness and event lag.

High-cardinality sample, prompt, and tensor contents MUST NOT enter telemetry by
default. Sensitive diagnostics require separate authorization and retention.

### 19.3 SLOs

OMF does not prescribe universal thresholds. A site MUST publish workload-class
SLOs and their measurement windows. At minimum, production classes cover:

- control-plane availability and event durability;
- run admission and scheduler placement;
- checkpoint RPO and restore time;
- evaluation lag;
- inference availability and latency;
- lineage freshness;
- incident detection and escalation.

## 20. Illustrative open-source binding

This table is informative, not mandatory. Component versions, licenses, and
security posture must be audited before implementation.

| Role | Local binding | Site/federated binding candidates |
| --- | --- | --- |
| Source/review | Git | Forgejo or another open Git forge |
| Orchestration | Embedded runner or Dagster local | Dagster, Argo Workflows, or Flyte adapter |
| Executor | Subprocess/container | Kubernetes or Slurm |
| Queue/admission | Local FIFO | Kueue; existing Slurm policy |
| Distributed job | Local process launcher | JobSet, Kubeflow Trainer, Ray, or native scheduler job |
| Artifact registry | Filesystem OCI layout | Harbor-compatible OCI registry |
| Blob storage | Filesystem | Ceph or another S3-compatible on-premises store |
| Data tables | Parquet/Arrow/DuckDB | Iceberg with Spark, Ray, or another compatible engine |
| Lineage | Embedded graph + OpenLineage events | OpenLineage-compatible backend plus OMF facets |
| Experiment/model registry | OMF manifests | MLflow-compatible adapter, without making it canonical |
| Training | PyTorch/JAX plugin | TorchTitan, OLMo-core, JAX, or framework-specific adapter |
| Inference | Framework runtime | vLLM, SGLang, KServe, Triton, or modality runtime adapter |
| Synthetic pipeline | Local plugin graph | Distilabel or distributed generation adapters |
| Sandbox | Restricted local VM | Firecracker, gVisor, Kata, or equivalent isolation service |
| Identity | Local development CA | SPIRE-compatible workload identity |
| Secrets/policy | Encrypted local store | OpenBao plus OPA or equivalent open components |
| Telemetry | OTLP collector | OpenTelemetry, Prometheus, Grafana, Loki, and Tempo |
| Packaging | OCI artifact | OCI registry; evaluate CNCF ModelPack as it matures |

No named candidate is part of OMF identity. Replacement is demonstrated by
passing the same contract suite.

## 21. Conformance

### 21.1 Profiles

Conformance claims list a specification revision and one or more profiles:

| Profile | Required evidence |
| --- | --- |
| `OMF-Core` | Clean-clone bootstrap, CLI/API, modules, data import/sync, immutable assets, events, lineage, policy, local end-to-end run |
| `OMF-Cluster` | Distributed binding, gang admission, checkpoint recovery, quotas, preemption |
| `OMF-Airgap` | Signed offline install, zero-egress operation, backup/restore, local identity and policy |
| `OMF-Federated` | Multi-cell placement, signed reconciliation, trust and residency enforcement |
| `OMF-Frontier` | `OMF-Cluster` or `OMF-Federated` plus reproducible capacity report at ≥1,024 accelerators |

Claims also name supported capability profiles, such as modality plugins,
trainers, inference runtimes, environments, and reproducibility classes.

For executable profile decisions, `OMF-Core` requires scenarios 1, 2, 4–12,
16, and 17 below, excluding cluster-only scenario 3 and site-specific scenarios
13–15. `OMF-Cluster` additionally requires scenarios 3 and 14. `OMF-Airgap`
additionally requires scenario 13. `OMF-Federated` additionally requires
scenario 15. `OMF-Frontier` requires a complete `OMF-Cluster` or
`OMF-Federated` result and an actual measured capacity report covering at least
1,024 accelerators. A report generator MUST deny rather than infer a claim when
any required scenario or measured field is absent.

### 21.2 Mandatory conformance scenarios

1. **Clone to model:** From a clean checkout, bootstrap without external
   services, validate a user module, import data, sync it to a second filesystem
   holding site, train, evaluate, inspect lineage, and create a signed release.
2. **Storage independence:** Push one immutable dataset to two different store
   adapters, verify identical logical/content identity, and reconstruct it in a
   second clean checkout without copying credentials through Git.
3. **Portable workload:** Run one compatible workload locally and on a cluster
   by changing only the binding. Compare lineage and declared numerical result.
4. **Complete derivation:** Trace a release upstream to source samples and
   downstream from a source to every dependent release/deployment.
5. **Sampler replay:** Reproduce a sampled prefix across restart and worker-count
   change according to the declared delivery guarantee.
6. **Live amendment:** Activate a new mix at a boundary, checkpoint it, and
   replay the complete timeline.
7. **Atomic recovery:** Fail workers during checkpoint write and training;
   restore only a committed checkpoint without hidden semantic changes.
8. **Train/serve parity:** Pass model-package conformance vectors against two
   independently optimized adapters.
9. **Checkpoint-triggered evaluation:** Materialize a checkpoint, independently
   schedule evaluations, retain partial failures, and record complete results.
10. **Promotion denial:** Demonstrate a failed rights or safety gate cannot move
   a release alias or deployment.
11. **Sandbox escape resistance:** Exercise isolation, egress, quota, secret,
    and verifier-separation controls with adversarial workloads.
12. **Revocation impact:** Revoke a source and enumerate/quarantine every
    affected artifact according to policy.
13. **Air gap:** Install and run a complete lifecycle with external egress
    denied; verify no call-home attempts.
14. **Preemption:** Reclaim a workload, resume it automatically, and account for
    lost work.
15. **Federation:** Disconnect a running cell, continue within lease, reconnect,
    and reconcile signed events without artifact-identity conflict.
16. **Model agnosticism:** Run at least two materially different modality or
    model-method plugins through the same lifecycle contracts.
17. **Governed feedback:** Capture a deployment signal, apply privacy and
    quality policy, materialize a new dataset revision, and prove it cannot
    enter training or update a deployment before explicit approval.

### 21.3 Conformance report

A report MUST include test suite revision, all manifests/digests, environment,
hardware, raw results, failures, waivers, and signatures. A partial profile may
be claimed only when every mandatory scenario for that profile passes.

## 22. Implementation sequence

### Phase 0 — Contracts and executable conformance

Deliver:

- versioned JSON Schemas for all core resources;
- canonicalization, identity, signing, and event libraries;
- cloneable workspace layout, generated `.gitignore`, and project validator;
- one CLI backed by the same local API used by automation;
- local artifact/event/lineage stores;
- filesystem connector/store, immutable snapshots, sync planner, and verified
  resumable copy;
- plugin SDK and capability negotiation;
- conformance CLI and deterministic fixtures.

Exit criterion: a clean clone bootstraps and passes `omf doctor`; schemas can
represent two different model kinds without model-specific fields in the core.

### Phase 1 — Local vertical slice

Deliver one-host implementations for module packaging, data copy/register,
immutable dataset, store synchronization, sampler, training, atomic checkpoint,
inference, evaluation, policy gate, and release.

Exit criterion: the complete clean-clone journey passes `OMF-Core`, including a
second holding site, restart, and full lineage. This phase intentionally
optimizes correctness before throughput.

### Phase 2 — On-premises cluster

Add object/OCI storage and a non-filesystem store adapter, HA metadata,
scheduler/executor adapters, distributed training, topology placement,
automated recovery, and local observability.

Exit criterion: the Phase 1 workload passes with a cluster binding only, and the
installation passes `OMF-Cluster` and `OMF-Airgap`.

### Phase 3 — Iteration flywheel

Add globally coherent streaming mixtures, mid-run amendments, synthetic graphs,
environment fleets, online RL roles, governed operational feedback, human
review, and optional fast policy-state publication.

Exit criterion: a data or objective change propagates from committed spec to
evaluation evidence without a bespoke pipeline or trainer/inference fork.

### Phase 4 — Federation and frontier evidence

Add cell federation, capacity brokerage, signed reconciliation, residency-aware
placement, large-scale load testing, and operational hardening.

Exit criterion: publish `OMF-Federated` evidence and a dated capacity report.
`OMF-Frontier` is claimed only after a qualifying measured run.

## 23. Graduation metrics

The project is ready to describe itself as a demonstrated model factory only
after publishing evidence for:

1. **Clone-to-model time:** clean checkout to first locally evaluated model.
2. **Cycle time:** committed workload to signed release.
3. **Storage portability:** the same asset survives verified push/pull across
   different holding-site adapters without identity change.
4. **Lineage completeness:** measured coverage at each supported granularity.
5. **Promotion cost:** a research result reaches serving without source forks.
6. **Binding portability:** the same workload runs locally and distributed.
7. **Failure survival:** preemption and hardware loss recover automatically.
8. **Mixture replay:** amendments and world-size changes remain attributable.
9. **Model diversity:** materially different model kinds share the lifecycle.
10. **Air-gap autonomy:** full operation has no external dependency.
11. **Scale:** declared capacity is reproduced by the published benchmark.
12. **Openness:** release artifacts, schemas, tests, lineage summaries,
    licenses, and provenance are independently inspectable.

## 24. Open decisions for v0.2

The implementation process must resolve these without weakening the invariants:

1. Final JSON Schemas and media types for every core resource.
2. Exact OpenLineage custom facets and W3C PROV mapping.
3. OCI artifact layout for sharded checkpoints and very large datasets.
4. Connector/store capability schema, sync manifest/chunk protocol, and mutable
   source snapshot semantics.
5. Counter-based sampler algorithm and world-size-independent lease protocol.
6. Model package ABI across Python-native and compiled runtimes.
7. Environment action/observation transport and snapshot semantics.
8. Cross-vendor checkpoint portability and tensor naming.
9. Federation lease, trust, policy-epoch, and reconciliation protocols.
10. License compatibility matrix for the reference implementation.
11. Public benchmark workloads for scale, recovery, and modality neutrality.
12. Project governance, contribution policy, and code/content licenses.

## 25. Standards and informative references

- [BCP 14 / RFC 2119 and RFC 8174](https://www.rfc-editor.org/info/bcp14)
- [RFC 8785: JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785)
- [CloudEvents](https://cloudevents.io/)
- [OCI Image Specification](https://github.com/opencontainers/image-spec/blob/main/spec.md)
- [OCI Distribution Specification](https://github.com/opencontainers/distribution-spec)
- [CNCF ModelPack](https://modelpack.org/) (informative; maturity must be
  evaluated)
- [W3C PROV overview](https://www.w3.org/TR/prov-overview/)
- [OpenLineage object model](https://openlineage.io/docs/spec/object-model/)
  and [facets](https://openlineage.io/docs/spec/facets)
- [MLCommons Croissant](https://mlcommons.org/working-groups/data/croissant/)
- [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/)
- [SPIFFE](https://spiffe.io/docs/latest/spiffe-about/overview/)
- [SLSA 1.2](https://slsa.dev/spec/v1.2/)
- [SPDX](https://spdx.dev/) and [CycloneDX](https://cyclonedx.org/)
- [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework)
- [Kueue](https://kueue.sigs.k8s.io/docs/overview/) and
  [JobSet](https://jobset.sigs.k8s.io/docs/overview/) as informative Kubernetes
  bindings
