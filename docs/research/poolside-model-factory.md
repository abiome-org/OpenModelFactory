# Poolside Model Factory: public evidence and OMF consequences

**Research date:** 2026-09-01  
**Purpose:** Preserve the boundary between Poolside's public claims, secondary
interpretation in `j8ckfi/library`, and original Open Model Factory (OMF) design.

## Executive finding

Poolside has publicly described a strong **process architecture** for model
development: versioned data and experiments, an asset-oriented control plane,
reusable training and inference systems, streaming data mixtures, automatic
checkpoint evaluation, execution-grounded RL, automated recovery, and human
inspection.

Poolside has not published a clonable Model Factory implementation. Its named
systems—Titan, Atlas, Blender, Hive, AutoMixer, Saucer, Podium, and its current
scheduler—remain internal. Public material does not specify their complete APIs,
schemas, security controls, multi-tenant model, on-premises deployment, or
federation design. Claims are vendor-authored and are not independent
reproductions.

OMF therefore adopts the evidenced lifecycle pattern, not the proprietary
topology. OMF's model neutrality, scale-invariant bindings, cell federation,
air-gap behavior, security model, resource schemas, and conformance profiles are
new design requirements.

## Evidence method

Sources are classified as:

1. **Primary:** Poolside's own technical report and six-part Model Factory blog
   series.
2. **Secondary:** the structured summaries in
   [`j8ckfi/library`](https://github.com/j8ckfi/library), which point to and
   interpret those primary sources.
3. **Independent standards:** specifications used to make OMF open and
   interoperable. These corroborate useful interface patterns, not Poolside's
   implementation.
4. **OMF design:** requirements inferred or introduced for this project. These
   must not be attributed to Poolside.

Reported figures below mean “Poolside reports,” not “independently measured.”

## What `j8ckfi/library` contributes

The library's Poolside shelf was introduced as one retrospective synthesis, not
as multiple independent confirmations. The most relevant records are:

- [Poolside Model Factory method](https://github.com/j8ckfi/library/blob/master/graph/methods/poolside-model-factory.md)
- [Laguna M.1/XS.2 report record](https://github.com/j8ckfi/library/blob/master/graph/papers/laguna-m1-xs2.md)
- [Blender streaming](https://github.com/j8ckfi/library/blob/master/graph/methods/blender-streaming.md)
- [Hive synthesis](https://github.com/j8ckfi/library/blob/master/graph/methods/hive-synth.md)
- [AutoMixer](https://github.com/j8ckfi/library/blob/master/graph/methods/automixer.md)
- [Industrial model building task](https://github.com/j8ckfi/library/blob/master/graph/tasks/industrial-model-building.md)
- [Small-lab recipe](https://github.com/j8ckfi/library/blob/master/graph/recipes/small-lab-model-factory.md)

The library briefly carried an Open Model Factory proposal:

- [initial sketch at `f7d7037a`](https://github.com/j8ckfi/library/blob/f7d7037a5ee27b7415ca882c6966243682c2fd74/docs/open-model-factory.md)
- [revised sketch at `948598e`](https://github.com/j8ckfi/library/blob/948598e18f67359f90e125b191a42cb5a59d266a/docs/open-model-factory.md)

It was then [deleted in `b111041`](https://github.com/j8ckfi/library/commit/b111041add9d5d267276f1785fb022c8a7c19eff)
because it was moving to a dedicated repository. The revised sketch established
the central OMF invariant: one role-oriented architecture from one accelerator
to a federation, with scaling performed by backend/resource bindings rather
than pipeline rewrites. The current [OMF specification](../../SPEC.md) preserves
that invariant and formalizes the interfaces and operational gaps the sketch
left open.

## Publicly evidenced Poolside architecture

### System definition and operating principles

The [Laguna technical report](https://poolside.ai/assets/laguna/laguna-m1-xs2-technical-report.pdf)
defines the Model Factory as “a tightly-integrated stack of versioned data,
training, evaluation, and inference components.” Poolside's
[introductory essay](https://poolside.ai/blog/introducing-the-model-factory)
describes its purpose as reducing manual interaction, increasing signal, and
shortening iteration.

Directly supported principles are:

- every data, architecture, and training experiment is committed code or
  configuration;
- each run has a unique identity;
- data, training, checkpoint, evaluation, and deployment assets share lineage;
- loosely coupled components are composed under one orchestrator;
- checkpoint creation can trigger independent inference and evaluations;
- research and production reuse definitions and services;
- common scheduling, failure, and recovery work is automated;
- model-quality telemetry, systems telemetry, and qualitative review are all
  used, but are distinct.

The core loop can be reconstructed as:

```diagram
┌─────────────┐    ┌───────────────┐    ┌──────────────┐
│ Commit spec │───▶│ Asset/control │───▶│ Data stream  │
└─────────────┘    │ plane         │    └──────┬───────┘
                   └───────────────┘           ▼
                                     ┌────────────────┐
                                     │ Train/checkpoint│
                                     └───────┬────────┘
                                             ▼
                     ┌────────────────────────────────────┐
                     │ Inference · eval · synth · RL actor│
                     └───────────────┬────────────────────┘
                                     ▼
                         ┌────────────────────────┐
                         │ Metrics + human review │
                         └───────────┬────────────┘
                                     ▼
                              next committed spec
```

This diagram is an OMF reconstruction, not a published Poolside service
topology.

### Control and lineage: Dagster

Poolside describes Dagster as the central asset/control plane:

- a job begins with registration of a configuration asset;
- all run inputs/configurations are code in a single repository;
- runs receive unique IDs;
- intermediate data products, checkpoints, evaluations, and deployments become
  assets or are connected to them;
- new checkpoint partitions trigger selected evaluations;
- lineage can connect a packed training token to source and transforms, and a
  deployment back to its training run.

**OMF consequence:** retain an asset/event graph and immutable run identity, but
specify the contract independently of Dagster. Sample-level lineage is a
declared capability with rights and scale constraints, not an unqualified
promise.

### Data: Spark, Iceberg, and streaming

Poolside's
[data essay](https://poolside.ai/blog/gathering-and-processing-raw-materials-for-the-model-factory)
describes:

- source-specific ingestion into a fixed asset form;
- one Iceberg table per dataset and Dagster asset-driven construction;
- Spark transforms and materialized intermediate assets;
- OCR, quality/metadata filters, learned filtering, fuzzy deduplication,
  dependency ordering, tokenization, and packing;
- immutable dataset assets with traceable filter decisions;
- manual data inspection throughout the process;
- reported baseline ingestion around 20 trillion tokens/day.

These are LLM/code-specific implementation choices around a general requirement:
typed, immutable dataset snapshots with scalable transforms, quality evidence,
rights, and lineage.

### Blender: replayable streaming mixtures

The same essay describes Blender as two gRPC services:

1. configure an immutable `BlendConfig` containing weighted `BlendSource`
   entries that point to Iceberg tables or snapshots; and
2. fetch and stream rows according to that configuration.

Sources can be finite, oversampled, or live. A request against an undersupplied
live source can wait. Responses carry offsets so prior data can be fetched to
investigate anomalies. Streaming avoids pre-materializing and redistributing a
single giant blended shard whenever weights or cluster size change.

The technical report adds a sidecar prefetch path and globally synchronized
composition for distributed training.

**OMF consequence:** `MixSpec` and `SamplerState` are first-class contracts.
OMF additionally specifies immutable amendment timelines, world-size change,
delivery guarantees, authorization, and checkpoint coupling—details not fully
specified publicly by Poolside.

### Hive: declarative synthetic data

The technical report describes Hive as a configurable synthetic pipeline with
inputs, metadata, generators, filters, validators, and pre/post-processing. It
compiles these into orchestrator/generator/judge interaction loops supporting
rewriting, domain conversion, cascades, and multi-turn rollout.

Poolside reports about 4.4T generated tokens in its source pool and about 13%
synthetic data in the Laguna XS.2 mixture.

**OMF consequence:** generalize this to `GenerationSpec`, where generators and
validators can produce any typed modality and can be models, simulators,
programs, or humans. Synthetic origin must not erase source rights.

### AutoMixer: experiment-driven mixture search

The technical report describes:

- candidate mixtures sampled around a prior with constraints;
- roughly 60 proxy 0.5B MoE models trained on roughly 60B tokens each;
- surrogate regressors for capability families;
- constrained optimization of weighted objectives.

Reported gains were not universal; targeted code/math gains included
commonsense regressions. This is evidence that automated mix selection is
multi-objective and policy-relative.

**OMF consequence:** mix optimization is an optional experiment generator that
publishes evidence and Pareto trade-offs. One proxy experiment is not an
“AutoMixer,” and no optimizer is part of the core factory.

### Titan: distributed training and recovery

Poolside's
[Titan essay](https://poolside.ai/blog/titan-the-model-factory-s-furnace)
and report describe a PyTorch training codebase seeded from TorchTitan. Titan:

- exposes one training entry point across pretraining, mid-training, SFT, and
  RL;
- composes data, fully sharded data, tensor, expert, and pipeline parallelism;
- runs from a single development node to large production jobs;
- is launched as a versioned workload and produces versioned assets;
- supplies reference model definitions/results to Atlas;
- runs node preflight checks, detects common distributed failures, replaces
  workers, and restores checkpoints;
- records detailed metrics/logs and performs cross-replica integrity checks.

The report says Poolside added more than 2,200 changes beyond TorchTitan,
including custom MoE kernels, distributed Muon, checkpointing, and
observability. TorchTitan is therefore a public seed, not an open Titan release.

**OMF consequence:** define trainer lifecycle and checkpoint contracts. Do not
clone Titan's name, hard-code a model family, or assume one trainer
implementation can optimize every model kind.

### Atlas: inference as a shared carrier

Poolside's
[inference/evaluation essay](https://poolside.ai/blog/the-carrier-and-the-beacon)
describes Atlas as a composition-oriented inference library that wraps open
components including vLLM and adds specialized implementations. It:

- serves evaluation, synthetic generation, RL, internal, and customer traffic;
- uses Titan model configuration/reference outputs for correctness comparison;
- provides platform-specific CUDA, ROCm, and Trainium implementations;
- supports fixed or elastic deployments;
- exposes a stable internal request API, replicas, routing, logs, and upstream
  lineage.

The technical report later describes Envoy and a custom deployment/session
orchestrator.

**OMF consequence:** use one model package and conformance vectors, not
necessarily the same optimized training and serving code. The common inference
contract must be typed and modality-neutral; chat completion is only an adapter.

### Saucer and code execution

Poolside's
[code-execution essay](https://poolside.ai/blog/designing-a-world-class-code-execution-environment)
describes:

- Saucer gRPC APIs for repository ingestion and arbitrary revision/file fetch;
- Kafka-compatible ingestion logs and read-optimized Git pack/index storage;
- heuristic and agent-based builds into OCI images;
- revision deltas represented as thin OCI layers over a base;
- execution sessions with low-level commands and high-level test/coverage
  operations;
- direct APIs and higher-level Task Engine abstractions;
- routing that exploits revision/image locality.

Poolside reports more than 800K repositories in the detailed essay and roughly
one million in broader material. The technical report mentions several thousand
live execution containers during RL. A separate essay claim of “tens of
millions of concurrent tasks” should not be equated with tens of millions of
simultaneously running sandboxes without further evidence.

**OMF consequence:** generalize a code repository to `EnvironmentSpec`: a
versioned, reproducible world with typed actions/observations and an isolated
verifier. OMF adds explicit zero-trust sandbox requirements because Poolside's
public security detail is insufficient for implementation.

### Evaluation and human inspection

Poolside describes evaluations as reusable assets, independently schedulable
against in-flight checkpoints. Evaluations can reuse inference, data, and code
execution. Results enter the model-metrics system. Podium provides dataset and
model inspection, malformed-sample discovery, checkpoint comparisons, and
structured subjective feedback.

The technical report also acknowledges that all four highlighted agentic
benchmarks are vulnerable to benchmark hacking to some degree and records
patched images/verifiers.

**OMF consequence:** evaluation protocols, harness changes, holdouts,
uncertainty, invalid runs, and human review are versioned evidence. Rewriting an
external benchmark requires differential validation; native integration alone
does not guarantee fidelity.

### Post-training, RL, and state transfer

Poolside's
[post-training essay](https://poolside.ai/blog/post-training-in-the-model-factory)
describes SFT as a composition of Blender, Titan, evaluation, scheduling, and
checkpoint services. RL adds task sources, actors, code execution, rewards,
trajectory streaming, training, and evaluation while retaining the same
configuration/orchestration path.

Poolside reports large-scale asynchronous actor/learner operation on separate
nodes. Its fast synchronization uses NCCL point-to-point operations and
GPUDirect RDMA across distinct training and inference meshes. The technical
report gives release-run details such as asynchronous broadcasts every two
optimizer steps, KV-cache reset at activation, and an accepted trajectory
staleness bound of ten optimizer steps.

**OMF consequence:** committed checkpoints remain the universal policy-state
transport. Direct device transfer is an optional optimized binding that must
still create attributable policy-state identities and activation boundaries.

### Scheduling evolution

The 2025 essays describe Kubernetes plus Volcano for gang scheduling, priority,
fair sharing, bin packing, preemption, elastic jobs, TTL, and backfill. The 2026
technical report says Poolside replaced Volcano's critical scheduling path with
an internal design because node-level eviction and etcd-backed topology caused
disruption and long placement tails. The newer design reportedly uses per-job
reclaim, FoundationDB topology state, observer reconciliation, and sticky
respawn.

**OMF consequence:** scheduling needs a role contract, not a Poolside clone.
Current open systems such as Kueue, JobSet, Kubernetes controllers, and Slurm
should be bound and measured before considering a custom scheduler.

## Reported scale and proper interpretation

| Poolside report | Public figure | What it does not prove |
| --- | ---: | --- |
| Cluster envelope | about 10,000 H200 GPUs | Hardware independence or public reproducibility |
| Laguna M.1 run | 6,144 H200 GPUs | Complete training configuration |
| Laguna XS.2 run | 2,048 H200 GPUs | Five-week greenfield factory construction |
| Scheduler workload range | one node to about 10^4 accelerators | Unbounded or federated scale |
| Data processing | about 2×10^13 tokens/day | Non-text modality throughput |
| Unique source pool | about 27T tokens | Public data, full rights, or source inventory |
| Training volume | more than 30T tokens/model | Independent reproduction |
| Code environments | about 800K–1M repositories | General-purpose environment protocol |
| XS.2 cycle | five weeks from training start to release | Time to build the platform or M.1 precursor |

OMF uses Poolside's order of magnitude as motivation. It requires each
installation to publish a reproducible capacity report instead of inheriting
these figures as claims.

## Poolside facts versus OMF design

| Topic | Public Poolside evidence | OMF addition |
| --- | --- | --- |
| Experiments | Versioned configuration/code and unique runs | Canonical JSON, separate workload/binding digests, schema migration |
| Lineage | Asset graph across data, checkpoints, eval, deployment | W3C PROV semantics, OpenLineage facets, revocation impact queries |
| Scaling | Single-node to large cluster; heterogeneous pools | One binding architecture, cells, federation, disconnected reconciliation |
| Model scope | Coding-focused LLMs/MoEs | Typed modality-neutral samples, methods, environments, and plugins |
| Data mixing | Immutable configs, weighted live sources, offsets | Exact sampler state, global ranges, amendments, world-size replay |
| Training | Titan lifecycle, distributed parallelism, recovery | Trainer capability ABI and backend-neutral atomic checkpoint contract |
| Inference | Atlas shares Titan definitions/reference | Canonical model package and mandatory cross-runtime conformance vectors |
| RL | Async actors/learner, execution rewards, direct weight transfer | Algorithm-neutral roles, policy generations, verifier trust separation |
| Evaluation | Automatic checkpoint eval and human review | Integrity protocol, contamination controls, uncertainty, promotion policy |
| Security | Isolated code environments mentioned | Explicit threat model, workload identity, signing, egress, least privilege |
| On-premises | Not publicly specified as a clonable product | Zero-SaaS install, signed offline bundle, local telemetry, backup/restore |
| Openness | Some model weights and upstream seeds are open | OSI-licensed required stack, open schemas/tests, signed evidence release |

## Important unknowns

The public sources do not provide enough information to reproduce:

1. internal APIs, schemas, databases, source code, deployment manifests, or
   exact component versions;
2. complete model architecture and training configurations for all releases;
3. source inventories, licenses, consent/privacy handling, synthetic prompts,
   teachers, judge thresholds, and released datasets;
4. exact sampler consistency and replay semantics across failures/world-size
   changes;
5. complete RL task splits, prompts, tools, reward composition, group sizes,
   hyperparameters, and rollout accounting;
6. multi-tenancy, IAM, secret management, encryption, supply-chain controls,
   egress policy, sandbox hardening, and incident response;
7. on-premises installation, upgrade, backup, recovery, air-gap, or customer
   topology;
8. federation, malicious-peer handling, residency, quota delegation, and trust;
9. independent validation of reported performance, quality, and cycle time.

OMF must make these explicit rather than filling gaps with claims about
Poolside.

## Open standards used to extend the pattern

No one standard is a model factory. OMF composes their stable concepts:

| Standard/project | OMF use | Limitation |
| --- | --- | --- |
| [W3C PROV](https://www.w3.org/TR/prov-overview/) | Entity/activity/agent derivation semantics | Not an execution or storage implementation |
| [OpenLineage](https://openlineage.io/docs/spec/object-model/) | Job/run/dataset event interchange and custom facets | Needs OMF resources for checkpoints, models, policy, and review |
| [CloudEvents](https://cloudevents.io/) | Common lifecycle event envelope | Does not define OMF event payloads or delivery |
| [OCI Image](https://github.com/opencontainers/image-spec/blob/main/spec.md) and [Distribution](https://github.com/opencontainers/distribution-spec) | Content-addressed packaging and transport | Large model/data layouts still need OMF manifests |
| [CNCF ModelPack](https://modelpack.org/) | Informative OCI AI packaging direction | Must be evaluated for maturity and OMF's frontier requirements |
| [MLCommons Croissant](https://mlcommons.org/working-groups/data/croissant/) | Portable dataset schema, provenance, and usage metadata | Requires modality/domain extensions and executable pipeline lineage |
| [SLSA 1.2](https://slsa.dev/spec/v1.2/) | Source/build provenance and supply-chain levels | Does not capture training-data or model-learning derivation |
| [SPIFFE](https://spiffe.io/docs/latest/spiffe-about/overview/) | Workload identity and federation semantics | Requires an implementation and local authorization policy |
| [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/) | Vendor-neutral traces, metrics, and logs | Telemetry is not provenance |
| [Kueue](https://kueue.sigs.k8s.io/docs/overview/) | Queueing, quota, preemption, topology, multi-cluster binding | Kubernetes-specific and not the scientific control plane |
| [JobSet](https://jobset.sigs.k8s.io/docs/overview/) | Multi-role distributed job lifecycle | Kubernetes-specific executor binding |
| [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework) | Informative Govern/Map/Measure/Manage risk structure | Voluntary framework; use does not establish compliance |

## Primary source index

1. [Laguna M.1/XS.2 technical report](https://poolside.ai/assets/laguna/laguna-m1-xs2-technical-report.pdf)
2. [The hidden engineering behind foundation model building](https://poolside.ai/blog/introducing-the-model-factory)
3. [Gathering and processing raw materials](https://poolside.ai/blog/gathering-and-processing-raw-materials-for-the-model-factory)
4. [Titan, the Model Factory's furnace](https://poolside.ai/blog/titan-the-model-factory-s-furnace)
5. [Designing a world-class code execution environment](https://poolside.ai/blog/designing-a-world-class-code-execution-environment)
6. [The carrier and the beacon](https://poolside.ai/blog/the-carrier-and-the-beacon)
7. [The finishing touches](https://poolside.ai/blog/post-training-in-the-model-factory)
8. [Poolside research hub](https://poolside.ai/research)
9. [TorchTitan](https://github.com/pytorch/torchtitan), the public seed Poolside
   says it extended—not the Titan source code

## Bottom line

The defensible insight from Poolside is not a list of products to imitate. It is
that model development becomes an industrial, fast-learning process when data,
training, inference, environments, evaluation, human judgment, and operations
are versioned assets under one automated evidence graph.

OMF's job is to make that pattern open, formally testable, model-neutral,
secure, portable from one host to a federation, and fully operable on premises.
