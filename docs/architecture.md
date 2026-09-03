# Architecture and extension boundaries

OMF is one application boundary with replaceable data, module, store, and
executor implementations. It keeps workload behavior portable while retaining
the exact physical execution and evidence that produced a result.

## Sources of truth

There is no monolithic prose specification. Machine-readable rules live with
the components that enforce them:

1. `factory/omf/schemas/`, `models.py`, and `schema_registry.py` define resource
   fields and serialization.
2. `factory/omf/`, the `omf.module/v1` protocol models, CLI, and HTTP API define
   executable behavior.
3. `tests/` supplies regression and integration evidence for the checked-in
   implementation.
4. `docs/` explains operation and extension boundaries; `manual/` provides the
   tested model-building path; `ROADMAP.md` describes future release criteria
   but does not define current behavior.

When these disagree, restore the implementation to the versioned format or
change the format, compatibility notes, implementation, and tests together.

A greenfield project begins with `MODEL_CARD.md`, a concise human record of
purpose, interface, benchmark targets, data boundaries, and risk. It guides the
resources below but is not another resource format or a substitute for measured
results.

## Lifecycle

```diagram
┌──────────────┐   ┌───────────┐   ┌──────────────┐   ┌──────────────┐
│ Git config   │──▶│ Validation│──▶│ Executor     │──▶│ Immutable    │
│ and modules  │   │ and exact │   │ and modules │   │ evidence     │
│              │   │ revisions │   │             │   │              │
└──────────────┘   └─────┬─────┘   └──────┬───────┘   └──────┬───────┘
                         │                │                  │
                         ▼                ▼                  ▼
                   policy gates     artifact stores    release/deploy
```

1. **Versioned configuration.** Resources describe what the model-building and
   evaluation work does. `WorkloadSpec`, `ModelPackage`, `EvaluationSpec`, and
   `MixSpec` do not contain scheduler, machine, or cloud configuration. A
   `Binding` owns physical resources, placement, transport, and provider
   options.
2. **Validation.** `Factory` validates semantics, records exact data, package,
   evaluation, mix, training-module, inference-adapter, dependency-lock,
   observed executable, and binding revisions, then rejects unsupported
   capabilities before allocation.
3. **Execution.** Modules communicate through `omf.module/v1`. Bindings select
   an exact executor provider; unknown or incomplete providers never fall back
   to local execution. A run uses its durable operation ID as stable identity.
4. **Evidence.** Signed events, bidirectional lineage, immutable `RunResult`,
   atomic `Checkpoint`, and `EvaluationResult` resources preserve what ran and
   what was measured. The current status view is guarded by transitions or
   compare-and-set versions.
5. **Governance.** Experiments compare exact evaluation revisions. Releases
   consume selected evidence and remain blocked on rights, vulnerability,
   approval, signature, lineage, and model-compatibility gates.

## Invariants

- Core remains neutral to architecture, modality, framework, language,
  hardware, scheduler, cloud, and storage provider.
- Resource revisions and payload identities are immutable. Human-readable
  aliases may move only through operations tied to a named actor and checked by
  policy.
- Retry, reconciliation, failover, and rebinding cannot silently change data,
  code, model shape, objective, precision policy, or evaluation semantics.
- Git stores code and versioned project configuration; governed artifact stores
  hold datasets, checkpoints, model payloads, and releases; `.omf/` holds
  untracked local runtime state.
- Credentials, raw sensitive content, prompts, model payloads, and operation or
  event payloads do not enter Git, errors, agent context, goals, or knowledge.
- A scheduler acceptance, provider name, or code path does not establish
  security, recovery, scale, air-gap, or federation behavior. Those properties
  need direct tests in the environment where they are supported.

## Ownership

- `factory.py` orchestrates lifecycle transitions used by both interfaces.
- `agent.py` owns bounded context, action descriptions, goals, and knowledge.
- `executors/` owns provider discovery, preflight, lifecycle, transport,
  cancellation, logs, and restart attachment.
- `database.py`, `events.py`, `lineage.py`, and `operations.py` own durable state
  and audit evidence.
- `artifacts.py`, `data.py`, `sync.py`, and `stores/` own payload identity and
  movement.
- `evaluation.py`, `policy.py`, `releases.py`, `deployments.py`, and deployment
  orchestration in `factory.py` own governed progression toward serving.

Extend workload behavior with a Module or ModelPackage adapter. Extend physical
placement with an `omf.executors` provider. Add modality or framework
conveniences as optional starter packs. Do not add model assumptions to core or
provider details to portable resources.

## Current executor boundary

Executor capabilities differ and must not be inferred from provider names. The
[executor guide](executors.md) is the source of truth for current built-in
capabilities, runtime restrictions, transport gaps, and provider acceptance
tests.
