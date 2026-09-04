# 5. Train and measure a baseline

**Status: Tested now**

## Outcome

You will produce one run tied to a named actor, with immutable module packages,
workload and binding digests, dataset lineage, stage outputs, and boolean
evaluation evidence. Use the
[canonical lifecycle](README.md#canonical-tested-local-lifecycle) for the tested
commands.

## Read the graph before spending compute

The current [from-scratch workload](../workloads/example-from-scratch.yaml) uses
the canonical `graph.stages` form. Each stage declares a module, operation,
inputs, semantic configuration, dependencies, and expected outputs. It also
references a `ModelPackage` and an `EvaluationSpec`. The training stage
consumes `dataset/example-affine`; the evaluator consumes the trained state.

The [local binding](../bindings/local.yaml) owns physical execution, resources,
stores, isolation, and recovery. Do not put provider or scheduler settings in
the workload.

Inventory and preflight the exact pair before allocation:

```sh
omf --output json executor list
omf --output json executor preflight bindings/local.yaml \
  --workload workloads/example-from-scratch.yaml
```

Preflight must report `ready: true` and all module transport/isolation
capabilities. It creates no run and is not evidence that the workload succeeded.

## Admit, observe, and evaluate

Run the committed graph with a named actor, retain the returned `runId`, and
inspect terminal status and lineage before evaluation:

```sh
omf --actor research-agent --output json run \
  workloads/example-from-scratch.yaml --binding bindings/local.yaml
omf --output json runs status <run-id>
omf --output json lineage show run:<run-id>/stage:train
omf --actor research-agent --output json evaluate run/<run-id>
```

A successful submit is not success. Require terminal `Succeeded`, complete
declared outputs, readable artifact manifests, non-empty input/module lineage,
and an evaluation result tied to that exact run.

The example checkpoint binds verified `module-state` and `protocol-state`
components. Checkpoint publication is not a restore claim.

## Baseline record

Retain at least:

- repository revision and actor;
- dataset resource revision and manifest digest;
- workload, binding, and admitted module digests;
- run ID and terminal status version;
- evaluator module and protocol revision;
- scores, artifacts, failures, resource observations, and limitations.

The current example demonstrates mechanics only. Its pass bit is not a general
benchmark, statistical significance result, or release-quality claim.
