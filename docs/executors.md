# Executor providers and portable workloads

OMF keeps scientific intent in a `WorkloadSpec` and physical execution policy
in a `Binding`. An executor provider is the replaceable bridge between that
binding and a process, scheduler, cloud runner, or capacity broker. Moving a
workload from a laptop to Slurm, Kubernetes, Modal, Vast.ai, or another backend
must not require editing the workload DAG or module protocol.

```diagram
┌──────────────┐     ┌──────────────┐     ┌───────────────────┐
│ Workload DAG │────▶│ Binding      │────▶│ Named provider    │
│ what/why     │     │ where/policy │     │ how to execute    │
└──────┬───────┘     └──────────────┘     └─────────┬─────────┘
       │                                             │
       ▼                                             ▼
┌──────────────┐                           ┌───────────────────┐
│ omf.module/v1│◀──────────────────────────│ runner/scheduler  │
│ stable I/O   │ request · result · assets │ local or remote   │
└──────────────┘                           └───────────────────┘
```

## Discover and preflight

Provider selection is exact and fail-closed. OMF never executes an unknown or
unready provider as `local`.

```sh
omf --output json executor list
omf --output json executor preflight bindings/local.yaml
omf --output json executor preflight bindings/site.yaml \
  --workload workloads/train.yaml
```

The catalog reports each provider's source, potentially supported capabilities,
and centrally validated configuration contract. Preflight instantiates the
configured provider and reports actual capabilities, missing workload
requirements, and host or control plane issues. When a workload is supplied,
preflight also includes requirements derived from its modules, such as
enforceable network denial. It creates no run identity, resource, or event.

The same interfaces are `GET /v1/executors` and
`POST /v1/executors/preflight`. Provider inventory is included in
`AgentContext`, so an agent can choose and diagnose an execution path without
reading implementation code first.

## Binding contract

Executor-specific values live under `spec.config.executor`; they never enter the
portable workload.

```yaml
apiVersion: omf.dev/v1alpha1
kind: Binding
metadata:
  name: slurm-shared
  namespace: local/my-factory
spec:
  executor: slurm
  resources:
    gpus: 8
  config:
    executor:
      sharedFilesystem: true
    stores:
      artifacts: primary
      checkpoints: primary
  placement:
    partition: gpu
  transport:
    moduleProtocol: shared-filesystem
```

The provider factory receives the complete immutable declaration as
`ExecutorContext.declaration`, repository and state roots, the attributable
actor, and only the nested executor options as `ExecutorContext.config`. A
provider may interpret binding resources, placement, transport, and policy, but
must not reinterpret stage semantics.

Configuration is desired state and must not contain plaintext credentials.
Refer to symbolic secrets or use the runner's workload identity. Provider code
is trusted runtime code: installing a Python entry point authorizes it to run in
the OMF service process.

## End-to-end module transport

Implementing scheduler submission is not enough to execute a workload. A
provider may advertise `omf.module/v1` only when it satisfies all four
capabilities:

| Capability | Required behavior |
| --- | --- |
| `protocol:omf.module/v1` | Preserve the protocol request/result contract |
| `transport:module-source` | Make the exact admitted source package available to the worker |
| `transport:request-result` | Deliver `request.json`; retrieve `result.json` before success |
| `transport:artifacts` | Retrieve declared artifacts into the stage run directory |

The controller writes `request.json` and passes local `run_dir`, admitted-source
`cwd`, argv, limits, timeout, and network policy to `Executor.plan`. A remote
provider must then:

1. stage or mount the exact `cwd` and `request.json`;
2. run argv without a shell unless its plan explicitly and safely quotes it;
3. set `OMF_REQUEST_FILE`, `OMF_RESULT_FILE`, and `OMF_RUN_ID` remotely;
4. durably map its scheduler identity to the local run directory;
5. retrieve result, logs, and every declared artifact before reporting
   `succeeded`;
6. keep artifact paths inside the local stage run directory;
7. implement observed status, cancellation, log retrieval, and restart attach;
8. report failure rather than fabricate a result or fall back to another
   provider.

If a module declares no network destinations, the provider also needs
`isolation:network-deny`. Assertions in configuration are not enforcement: the
adapter must actually provide the isolation boundary.

## Built-in providers

| Provider | What is implemented | Complete workload status |
| --- | --- | --- |
| `local` | POSIX process groups, limits, timeout, durable status/logs, local protocol and artifact transport | Ready when host preflight and requested network isolation pass |
| `slurm` | `sbatch`/`sacct`/`scancel`, deterministic scripts, request/result environment, shared-filesystem transport | Requires `sharedFilesystem: true`; built-in adapter cannot enforce network denial |
| `kubernetes` | Immutable-image Job/JobSet plans and scheduler lifecycle | Not workload-ready: source, request/result, and artifact transport are intentionally absent |

These statements are capability facts, not product preferences. A site can
extend an adapter in this clone, inject a registry in an embedding service, or
install a separate provider package. A Kubernetes provider that adds an init
container/object-store transport and result collector should use a distinct
name until it fully replaces the built-in contract in that clone.

Deployments use the same registry and durable attach contract. A deployment
defaults to `local`; select another provider with
`spec.extensions.executor` and place its options in
`spec.extensions.executorConfig`. The provider must advertise
`protocol:omf.deployment/v1`. This prevents a hidden local execution path while
keeping deployment-form semantics separate from scheduler details.

## Add a provider

The smallest in-repository change is to construct and inject an
`ExecutorRegistry`; this is useful for a site service or tests. A reusable
provider is an installed Python package exporting one `ExecutorProvider` entry
point:

```python
# omf_modal/provider.py
from omf.executors import (
    MODULE_PROTOCOL_CAPABILITIES,
    ExecutorContext,
    ExecutorProvider,
)
from .executor import ModalExecutor


def create(context: ExecutorContext) -> ModalExecutor:
    # Resolve workload identity in the Modal client; do not put a token in the binding.
    return ModalExecutor(
        project_root=context.project_root,
        image=str(context.config["image"]),
        environment=str(context.config.get("environment", "main")),
    )


provider = ExecutorProvider(
    name="modal",
    factory=create,
    description="Modal function runner with object-store protocol transport.",
    capabilities=MODULE_PROTOCOL_CAPABILITIES,
    config_contract={
        "type": "object",
        "required": ["image"],
        "properties": {
            "image": {"type": "string"},
            "environment": {"type": "string"},
        },
        "additionalProperties": False,
    },
)
```

```toml
# provider package pyproject.toml
[project.entry-points."omf.executors"]
modal = "omf_modal.provider:provider"
```

`ModalExecutor` implements the `Executor` abstract methods in
`omf.executors.base`: `capabilities`, `preflight`, `plan`, `submit`, `status`,
`cancel`, and `logs`; override `attach` when controller-local bookkeeping must
be reconstructed after restart. `plan` must be deterministic for identical
inputs. Entry-point name and provider name must match. Duplicate names and
invalid provider objects fail rather than silently replacing code.

For a repository-local provider, add the class under `factory/omf/executors/`
and register it in `default_executor_registry`. For embedding without changing
defaults:

```python
registry = ExecutorRegistry()
registry.register(provider, source="site")
factory = Factory(paths, executors=registry)
```

Register every provider the embedding needs; injection replaces, rather than
augments, the default registry. This makes the active trust boundary explicit.

## Backend design guidance

- **Modal:** package admitted source or build an immutable image, upload request
  and inputs by digest, use a durable call ID, and download results/artifacts
  before terminal success. Keep Modal secrets in its environment, not Git.
- **Vast.ai:** treat instance provisioning and command execution as one durable
  provider state machine. Verify image digests, host keys, staged content, and
  retrieval before releasing capacity; tolerate instance disappearance.
- **Kubernetes:** prefer immutable images, workload identity, Jobs/JobSets, an
  init/sidecar or object-store protocol transport, explicit topology/resources,
  and a collector that makes terminal results durable before Job cleanup.
- **Slurm:** use a shared filesystem only when every compute node sees the same
  absolute paths and locking semantics. Otherwise add explicit staging. Map
  preemption signals to checkpoint hooks and never claim network denial unless
  site isolation enforces it.

## Provider conformance checklist

Before a binding is production-eligible, test:

- exact provider selection, unknown-provider failure, and no local fallback;
- clean preflight failure before run allocation;
- immutable source/request staging and digest verification;
- successful result and multi-artifact retrieval;
- nonzero exit, timeout, cancellation, lost worker, and lost controller;
- attach/reconciliation after service restart;
- bounded logs without credential or payload leakage;
- network, identity, secret, quota, and resource enforcement;
- checkpoint commit/recovery and idempotent retry semantics;
- the same representative workload unchanged on local and the target binding;
- measured scale, cost, and recovery evidence required by `SPEC.md`.

Do not infer conformance from a provider name or a successful scheduler submit.
The signed scenario evidence is the claim.
