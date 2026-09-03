# 9. Rebind execution

**Status: Conditional**

Built-in executor capabilities differ, and external providers must demonstrate
their advertised behavior. Read the [executor guide](../docs/executors.md) for
the exact current limitations before changing a binding.

## Keep workload behavior stable

Keep stages, modules, dataset references, semantic parameters, and evaluation
protocol in the workload. Put executor, resources, topology, placement,
transport, stores, isolation, recovery, and provider-specific options in the
binding. Rebinding should not require a workload fork.

Inventory trusted provider code and preflight the exact workload before
allocation:

```sh
omf --output json executor list
omf --output json executor preflight bindings/site.yaml \
  --workload workloads/candidate.yaml
```

Unknown providers, missing tools, incomplete protocol transport, or
unenforceable required isolation must stop with an error. Never silently
substitute local execution.

## Provider acceptance checklist

- Exact admitted module source reaches the worker.
- Request and result envelopes survive transport unchanged.
- Declared input and output artifacts are transferred and digest-verified.
- Terminal success is reported only after durable result retrieval.
- Status, logs, cancellation, timeout, restart, and reconciliation are tested.
- Identity, secrets, network, quotas, and resource limits are enforced.
- Checkpoint publication and recovery preserve the declared semantics.

Rebinding can change timing and numerical behavior. Record realized topology,
library/runtime revisions, precision, batch semantics, seeds, and divergences.
Shape reduction, changed global batch/objective semantics, or changed sampling
changes what the workload does; it is not merely another placement of the same
run.

Scheduler acceptance or a completed job does not prove the provider path is
supported. Add its transport, recovery, isolation, and scale cases to the test
matrix described by the [executor guide](../docs/executors.md).
