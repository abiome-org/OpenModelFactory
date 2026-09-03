# Executor providers and portable workloads

OMF keeps what a workload does in `WorkloadSpec`. A `Binding` selects the
execution provider, resources, placement, transport, and site policy. An
executor provider is the replaceable bridge between that binding and a process,
scheduler, cloud runner, or capacity broker. Moving a workload from a laptop to
Slurm, Kubernetes, Modal, Vast.ai, or another backend must not require editing
the workload DAG or module protocol.

```diagram
┌──────────────┐     ┌──────────────┐     ┌───────────────────┐
│ Workload DAG │────▶│ Binding      │────▶│ Named provider    │
│ behavior     │     │ place/policy │     │ how to execute    │
└──────┬───────┘     └──────────────┘     └─────────┬─────────┘
       │                                             │
       ▼                                             ▼
┌──────────────┐                           ┌───────────────────┐
│ omf.module/v1│◀──────────────────────────│ runner/scheduler  │
│ stable I/O   │ request · result · assets │ local or remote   │
└──────────────┘                           └───────────────────┘
```

## Discover and preflight

Provider selection is exact. OMF returns an error for an unknown or unready
provider instead of silently running it as `local`.

```sh
omf --output json executor list
omf --output json executor preflight bindings/local.yaml
omf --output json executor preflight bindings/site.yaml \
  --workload workloads/train.yaml
```

The catalog reports each provider's source, potentially supported capabilities,
and centrally validated configuration. Preflight instantiates the
configured provider and reports actual capabilities, missing workload
requirements, and host or control plane issues. When a workload is supplied,
preflight also includes requirements derived from its modules, such as
enforceable network denial. It creates no run identity, resource, or event.

The same interfaces are `GET /v1/executors` and
`POST /v1/executors/preflight`. Provider inventory is included in
`AgentContext`, so an agent can choose and diagnose an execution path without
reading implementation code first.

## Binding format

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

The provider factory receives the complete declaration as an isolated deep copy
in `ExecutorContext.declaration`, repository and state roots, the named actor,
and only the nested executor options as `ExecutorContext.config`. A provider
may interpret binding resources, placement, transport, and policy, but must not
reinterpret stage semantics.

Versioned configuration must not contain plaintext credentials. Refer to
symbolic secrets or use the runner's workload identity. Provider code is trusted
runtime code: installing a Python entry point authorizes it to run in the OMF
service process.

## End-to-end module transport

Implementing scheduler submission is not enough to execute a workload. A
provider may advertise `omf.module/v1` only when it satisfies all four
capabilities:

| Capability | Required behavior |
| --- | --- |
| `protocol:omf.module/v1` | Preserve the protocol request and result |
| `transport:module-source` | Make the exact admitted source package available to the worker |
| `transport:request-result` | Deliver `request.json`; retrieve `result.json` before success |
| `transport:artifacts` | Retrieve declared artifacts into the stage run directory |

Execution-environment claims are separate capabilities. The local provider
launches the interpreter through the path the module named, keeping a virtual
environment's symlink intact so its site-packages remain visible, and records
the digest of the resolved executable. Its worker rechecks that pathname
immediately before process creation and advertises this honestly as
`environment:executable-drift-detection`, not byte-sealed attestation: trusted
host administration could still replace the executable in the final check/exec
interval.

A module with a non-empty dependency lock is realized by the local provider
(`environment:dependency-lock-realization`) into a cached virtual environment
under `.omf/environments/`, keyed by the lock digest, the interpreter digest,
and the realization options. The lock must name a Python interpreter entry
point and must pin every distribution with a hash; pip installs it with
`--require-hashes --only-binary=:all:`. After installation the environment
inherits the site directories of the interpreter the module named, so
`omf.sdk` and the project's toolchain stay importable while the lock always
shadows them. The recorded environment descriptor lists every distribution the
module can import, inherited layers included. Set
`spec.config.executor.dependencyWheelhouse` to install from a local wheel
directory and `dependencyIndex: false` to forbid index access; otherwise pip's
own configuration, including `PIP_*` variables, applies. Slurm and Kubernetes
advertise no environment realization in their built-in forms. None of these
built-ins claim a content-addressed runtime closure or bitwise environment
reproducibility.

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
| `local` | POSIX process groups, limits, timeout, durable status/logs, local protocol/artifact transport, executable drift detection, and hash-pinned dependency lock realization into cached virtual environments | Ready when host preflight, requested network isolation, and any lock installation pass |
| `slurm` | `sbatch`/`sacct`/`scancel`, deterministic scripts, request/result environment, shared-filesystem transport | Scheduler-lifecycle-only for modules because the built-in adapter cannot attest environments or enforce network denial |
| `kubernetes` | Immutable-image Job/JobSet plans and scheduler lifecycle | Not workload-ready: source, request/result, and artifact transport are intentionally absent |

These statements are capability facts, not product preferences. A site can
extend an adapter in this clone, inject a registry in an embedding service, or
install a separate provider package. A Kubernetes provider that adds an init
container/object-store transport and result collector should use a distinct
name until it fully replaces the built-in behavior in that clone.

Deployments use the same registry and durable restart attachment. A deployment
defaults to `local`; select another provider with
`spec.extensions.executor` and place its options in
`spec.extensions.executorConfig`. The provider must advertise
`protocol:omf.deployment/v1`. This prevents a hidden local execution path while
keeping deployment-form semantics separate from scheduler details.

## Add a provider

`omf.executor/v1` is the stable provider boundary. The controller passes an
isolated copy of project and desired-state context to the provider factory,
validates provider configuration before calling it, and then uses only the
exported `Executor` methods. Plans must be deterministic. `submit` returns a
durable provider ID. Providers that keep state in `run_dir` make `attach`
idempotent and verify that ID against the directory; scheduler-identity-only
providers can use the no-op default. `recover` returns `None` only when no
allocation occurred and raises when launch outcome is ambiguous. Status and
logs remain available after controller restart.

The smallest in-repository change is to construct and inject an
`ExecutorRegistry`; this is useful for a site service or tests. A reusable
provider is an installed Python package exporting one `ExecutorProvider` entry
point:

```python
# omf_modal/provider.py
from omf.executors import (
    EXECUTOR_API_VERSION,
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
    api_version=EXECUTOR_API_VERSION,
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

The entry point must export the current `EXECUTOR_API_VERSION`; missing or
unsupported versions fail during discovery rather than running against an
ambiguous interface. `ModalExecutor` implements the `Executor` abstract methods in
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

## Provider acceptance tests

Before supporting a binding for production work, test:

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
- measured scale, cost, and recovery behavior at the supported limits.

Do not infer support from a provider name or a successful scheduler submit. Add
the provider and its failure cases to the release test matrix.
