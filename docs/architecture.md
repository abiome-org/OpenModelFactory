# Architecture

OMF is one application boundary, `Factory`, with replaceable modules, stores,
and executors around it. This page is the documentation of the code: what each
part owns and the decisions that are not obvious from reading it.

## Lifecycle

```diagram
┌──────────────┐   ┌───────────┐   ┌──────────────┐   ┌──────────────┐
│ Git config   │──▶│ Admission │──▶│ Executor     │──▶│ Immutable    │
│ and modules  │   │ and exact │   │ and modules  │   │ evidence     │
│              │   │ revisions │   │              │   │              │
└──────────────┘   └─────┬─────┘   └──────┬───────┘   └──────┬───────┘
                         │                │                  │
                         ▼                ▼                  ▼
                   policy gates     artifact stores    release/deploy
```

1. Versioned manifests describe what the work does. `WorkloadSpec`,
   `ModelPackage`, and `EvaluationSpec` contain no machine or provider detail;
   a `Binding` owns limits and provider options.
2. Admission validates semantics, pins every input to an exact revision,
   captures module sources, prepares environments, and records the result in
   an immutable `Run` before allocating compute.
3. Execution runs each stage as one `omf.module/v1` exchange through the
   binding's executor. Unknown or incomplete providers never fall back to
   local execution. A run's identity is its durable operation id.
4. Evidence is immutable: signed events, bidirectional lineage, `RunResult`,
   `Checkpoint`, `EvaluationResult`, `Experiment`, and `Release`. Mutable
   status is guarded by state transitions or compare-and-set versions.
5. Governance gates promotion and deployment on rights, evaluation,
   compatibility, lineage, vulnerability, approval, signature, and policy.

## Invariants

- Core stays neutral to architecture, modality, framework, language, hardware,
  scheduler, cloud, and storage provider.
- Resource revisions and payload identities are immutable; aliases move only
  through attributed, policy-checked operations.
- Retry, recovery, and rebinding never silently change data, code, model
  shape, objective, or evaluation semantics.
- Git holds code and configuration; artifact stores hold payloads; `.omf/`
  holds untracked runtime state.
- Credentials, raw sensitive content, model payloads, and operation or event
  payloads never enter Git, errors, agent context, goals, or knowledge.
- Nothing is supported because a code path or provider name exists; only what
  direct tests demonstrate is supported.

## Code map

### Identity and serialization

- `canonical.py` parses strict JSON and safe YAML without interpolation or
  duplicate keys, round-tripping through JSON so YAML-only values such as
  dates and sets are rejected; it produces RFC 8785 canonical JSON, the
  `sha256:` digests derived from it, and validates portable repository-relative
  paths.
- `ids.py` creates RFC 9562 UUIDv7 values that are monotonic within a process
  and parses `algorithm:value` digests.
- `models.py` holds the common metadata and resource envelope;
  `finalize_resource` derives the revision from `apiVersion`, `kind`, name,
  namespace, and `spec` only, so status never changes identity.
- `schema_registry.py` loads the bundled JSON Schemas, merges the shared
  metadata and extension definitions, validates with JSON-path error details,
  and finalizes authored resources.
- `schemas/` holds one schema per kind. Every field a schema requires is one
  the runtime reads; `extensions` is the free-form escape hatch.

### Durable state

- `database.py` is the SQLite control plane: one connection per thread, WAL,
  checksummed migrations whose recorded history must match the bundled
  registry exactly, immutable resources keyed by uid and revision, statuses
  and aliases with compare-and-set versions, and read-only inspection for
  backups.
- `events.py` appends signed CloudEvents through a transactional outbox and
  serves bounded windows with incremental cursors.
- `lineage.py` stores PROV-shaped edges between runs, stages, artifacts, and
  resources and answers upstream and downstream queries.
- `operations.py` records long-running operations for detach, reattach, and
  reconciliation; `_operation_lease` in `factory.py` excludes concurrent
  workers on one operation and makes an abandoned running record detectably
  stale.
- `security.py` owns the Ed25519 signing identity, the encrypted secret store,
  and hashed, scoped, expiring API tokens.
- `backups.py` writes one archive with a signed inventory of metadata,
  identity, secrets, and local artifacts, and restores it atomically into a
  project with no `.omf/` after verifying every digest, the migration history,
  and the signing identity.

### Payloads

- `artifacts.py` defines chunked content-addressed manifests for blobs and
  directory trees, builds and verifies them, restores them into a temporary
  directory that is renamed into place, verifies restored trees, and publishes
  checkpoints only after every component manifest verifies.
- `stores/` implements the filesystem store (safe content-addressed layout
  with quarantine and garbage collection) and the S3 store (independent
  post-upload verification, conditional manifest publication).
- `sync.py` plans and executes resumable chunk transfers between stores and
  publishes the manifest last.
- `data.py` implements the snapshot ingestion modes.

### Modules and workloads

- `modules.py` projects a `Module` manifest, confines the code root and the
  lock inside the project, verifies the lock digest, checks contracts, packages
  a directory into a byte-reproducible tar without links or special files,
  extracts packages without traversal, describes the Git worktree (a
  repository without a commit has no HEAD, so every file counts as
  uncommitted), and scaffolds new modules.
- `sdk.py` is the language-neutral protocol model and the Python dispatcher:
  it always writes a result, even for an unhandled exception, so the executor
  can distinguish a module error from a transport failure.
- `workloads.py` projects a `WorkloadSpec` into an admitted graph with a
  topological order, keeps the per-run state history atomic, verifies it
  against the admitted spec on recovery, and runs stages in order.

### Execution

- `executors/base.py` is the `omf.executor/v1` contract: capability names,
  the execution plan, and the `Executor` methods. Scheduler submission alone
  is deliberately not enough to advertise the module protocol.
- `executors/registry.py` discovers trusted `omf.executors` entry points,
  validates provider configuration against each provider's contract, refuses
  controller-owned plan fields in provider options, and resolves bindings by
  exact name.
- `executors/local.py` runs process groups without a shell, applies the
  binding's POSIX limits and timeout, denies network through an unprivileged
  user namespace, and keeps durable status and log tails. It launches the
  interpreter through the path the module named rather than the resolved
  symlink target, because a virtual environment's interpreter is a symlink to
  its base and executing the target would drop the environment's site
  packages; the resolved bytes are attested by digest instead. A non-empty
  dependency lock is realized once into a cached environment keyed by lock,
  interpreter, and options, with the interpreter's site directories appended
  after the lock so `omf.sdk` stays importable while the lock shadows them.
- `executors/local_worker.py` is the detached monitor: it re-hashes attested
  executables immediately before exec, bounds log tails, forwards stop
  signals, and records completion durably.
- `run_worker.py` executes a detached run operation; `serve_worker.py` is the
  HTTP worker that serves a release through its inference adapter, one
  protocol exchange per request, never echoing request values in errors.

### Application

- `factory.py` is the application boundary shared by the CLI and the API. It
  loads and enforces the project policy, stamps namespaces, authorizes
  actions, admits and executes runs in phases (desired state, pinning, source
  capture, environment admission, state initialization, stage execution,
  result publication), reconciles interrupted operations only from immutable
  results, evaluates runs, builds and promotes releases, and applies, cancels,
  and rolls back deployments.
- `policy.py` is the deny-overrides rule engine, the project policy loader
  that rejects any configuration the factory cannot enforce, and the
  promotion gate.
- `releases.py` builds and verifies signed release manifests and moves aliases
  with a recorded policy decision.
- `agent.py` assembles the bounded agent context, the digest-addressed action
  catalog, fixed-rule recommendations, goals with guarded status, and
  evidence-backed knowledge.
- `cli.py` and `api.py` are two attributed interfaces over the same `Factory`;
  neither carries interface-specific semantics.
- `config.py` discovers the project, loads `omf.yaml`, and bootstraps `.omf/`.
- `install_support.py` holds the installer's atomic file operations and the
  starter copy; `install.sh` drives them.

## Extension points

Extend workload behavior with a module or a model package adapter. Extend
placement with an `omf.executors` provider. Add modality or framework
conveniences as modules. Do not add model assumptions to core or provider
details to portable resources.
