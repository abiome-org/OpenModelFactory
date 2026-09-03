# Open Model Factory roadmap

Open Model Factory is currently `0.1.x` **Alpha**. This roadmap advances the
real product: a repository that can begin with a model card, support repeated
model and data changes, benchmark candidates, and carry a selected result into
release and deployment.

Release requirements belong in the normal test suite. Tests that need remote or
specialized infrastructure run in the release matrix on named environments;
they do not create a separate product subsystem. Milestones are gated by passing
behavior, not dates or feature labels.

## Release progression

| Release line | Maturity | Change policy | Exit signal |
| --- | --- | --- | --- |
| `0.1.x` | Alpha, current | Breaking changes allowed when explicit | Checked-in local development loop passes |
| `0.2.x` | Alpha hardening | Persisted changes include migration tests | Greenfield and repeated-iteration tests pass |
| `0.5.x` | Beta | Supported files and interfaces keep working across Beta upgrades | Local and one remote path pass the release matrix |
| `0.9.x` | Release candidate | Only release-blocking fixes | Candidate survives independent install and upgrade rehearsals |
| `1.0.0` | Stable | Semantic versioning and published support windows | Release suite, artifacts, and support commitments are complete |

## Current baseline — `0.1.x`

The repository provides:

- non-destructive project installation with a living model card and local
  operating guidance;
- a tested local path through module and data admission, training, model
  compatibility checks, evaluation, and candidate comparison;
- immutable resource revisions, content-addressed artifacts, signed events,
  bidirectional lineage, and guarded status updates;
- one application implementation behind the CLI and HTTP API;
- filesystem and S3 artifact stores with planned, resumable synchronization;
- exact executor selection and no silent local fallback;
- policy-gated release and deployment paths; and
- locked dependencies, strict type checking, branch coverage, and package
  builds in the test workflow.

Known gaps include complete runtime-environment capture, sampler replay tied to
checkpoints, interrupted-run restore, independent training/serving adapter
checks, stronger local isolation, complete remote module transport, and real
production benchmark coverage.

## `0.2.x` — Alpha hardening

Goal: make a greenfield project and its repeated development loop dependable.

Required work:

- test a fresh install from `MODEL_CARD.md` through the first benchmarked
  baseline and a compared candidate;
- provide concise CI examples that rerun compatibility, evaluation, lineage,
  and policy checks when model, data, or workload inputs change;
- add migration and round-trip tests for every supported persisted-format
  change;
- capture complete dependency and runtime environments rather than only the
  executable path;
- integrate sampler state with atomic checkpoints and test replay across
  restarts and worker-count changes;
- restore interrupted work only from committed checkpoints and account for
  discarded work;
- run compatibility vectors through independently implemented training and
  serving adapters; and
- add adversarial tests for isolation, source revocation, and feedback-data
  admission.

Exit criteria:

1. `make test-all` passes from a clean clone on every supported Python version.
2. The greenfield test creates, trains, evaluates, compares, and traces a model
   without undocumented setup.
3. Upgrade tests cover the oldest supported `0.1.x` state and failed-migration
   recovery.
4. Every documented local capability has a direct test and no undeclared
   external dependency.

## `0.5.x` — Beta

Goal: make supported files, commands, APIs, and extensions dependable across
upgrades, and support one real remote execution path.

Required work:

- maintain upgrade tests for project resources, module protocol, executor
  plugins, CLI commands, HTTP endpoints, and events;
- complete one remote provider's source, request/result, artifact, identity,
  secret, isolation, cancellation, and restart transport;
- test preemption recovery, quota enforcement, topology placement, bounded
  logs, and controller restart attachment on that provider;
- run the same benchmark workload locally and remotely without changing its
  stages or evaluation rules;
- test offline installation and backup/restore with the original signing
  identity; and
- retain benchmark trends for quality, throughput, recovery time, and cost so
  regressions block release.

Exit criteria:

1. The release matrix passes on every supported Python version and deployment
   environment.
2. The supported remote provider completes and recovers the representative
   workload without a local fallback.
3. A fresh operator can install, upgrade, diagnose, restore, and roll back from
   published artifacts and focused documentation.
4. An incompatible change has advance warning, a migration path, and a defined
   removal release.

## `0.9.x` — Release candidate

A release candidate requires:

- no open release-blocking correctness, security, data-integrity, or recovery
  defects;
- reproducible source and wheel builds with checksums, SBOMs, provenance,
  signatures, and vulnerability review;
- passing clean-install, upgrade, backup/restore, and rollback rehearsals using
  candidate artifacts;
- independently repeated local and remote release tests, including fault and
  policy-denial paths;
- a published support matrix, security process, operational targets, and known
  limitations; and
- examples and documentation tested against installed candidate binaries.

A fix restarts the candidate when it changes a supported interface or
invalidates prior test results.

## `1.0.0` — Stable

The first stable release requires all release-candidate criteria plus:

1. signed source and wheel artifacts, checksums, SBOMs, provenance, and release
   notes from a tagged revision;
2. a supported local lifecycle and at least one supported remote execution path
   with explicit version and platform bounds;
3. migration and rollback tests from every supported pre-1.0 release;
4. semantic-versioning, deprecation, security-fix, and end-of-support policies;
5. benchmark gates for the supported model-development and deployment paths;
6. no correctness or capability statement broader than the tests behind it;
   and
7. documentation limited to the overview, tested workflow, operations,
   architecture, extension boundaries, and generated interface reference.

Federation, additional providers, modalities, frameworks, and larger scale can
ship later without changing the core lifecycle. They become supported only when
their behavior and failure modes join the ordinary release suite.

## Maintaining this roadmap

- Move an item only with a linked implementation and passing test.
- Put current limitations in the guide that owns the behavior.
- Add scope only when it serves the product loop or an exit criterion.
- Prefer deleting stale plans and duplicated prose over adding another guide.
