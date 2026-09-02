# Architecture and extension boundaries

`SPEC.md` is normative. This page is the short implementation map.

1. **Authoring contracts** — versioned resources in `factory/omf/schemas/` describe
   scientific intent. `WorkloadSpec`, `ModelPackage`, `EvaluationSpec`, and
   `MixSpec` never contain scheduler or cloud configuration.
2. **Admission** — `Factory` validates semantics, pins immutable data, package,
   evaluation, mix, module-source, opaque dependency-lock, observed executable,
   and Binding revisions, then rejects unsupported capabilities before allocation.
3. **Execution** — modules communicate only through `omf.module/v1`. Bindings select
   an explicit executor provider; unknown or incomplete providers never fall back
   to local execution. A run uses its durable operation ID as its stable identity;
   restart reconciliation repairs published completion evidence but never
   automatically replays work with an indeterminate outcome.
4. **Evidence** — signed events, bidirectional lineage, immutable `RunResult`,
   atomic `Checkpoint`, and `EvaluationResult` resources preserve what ran and
   what was measured. Checkpoint components are verified artifacts with explicit
   roles. `SamplerState` is reserved for sampler integrations that report observed
   replay state; samplerless runs record `replay.status: not-claimed` instead of
   fabricating it. Mutable status is only an operational projection.
5. **Governance** — experiments compare exact evaluation revisions. Releases consume
   explicitly selected evidence and remain blocked on rights, vulnerability,
   approval, signature, lineage, and conformance gates.

Extend scientific behavior with a Module or ModelPackage adapter. Extend physical
placement with an `omf.executors` provider. Add modality/framework conveniences as
optional starter packs. Do not add model assumptions to core or provider details to
portable resources.

Built-in truth: local execution supports zero-byte dependency locks and detects
executable-path drift, but does not seal an executable or dependency closure.
Slurm has scheduler lifecycle and an explicit shared-filesystem transport but does
not realize dependency environments; Kubernetes is scheduler-lifecycle-only.
Neither claims complete remote execution.
