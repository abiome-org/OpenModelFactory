# 9. Rebind execution

**Status: Conditional**

Local execution is tested. Built-in Slurm is conditional on explicit shared
storage and cannot enforce network denial. Built-in Kubernetes provides
scheduler lifecycle but lacks complete module source, request/result, and
artifact transport. External providers must prove their own capabilities.

## Preserve scientific intent

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
unenforceable required isolation must fail closed. Never silently substitute
local execution.

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
creates a new scientific workload; it is not merely another placement of the
same run.

Scheduler acceptance or a completed job is not conformance evidence. Retain the
measured scenario results required by the [executor guide](../docs/executors.md)
and [specification](../SPEC.md).
