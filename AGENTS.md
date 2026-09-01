# Open Model Factory agent guide

This repository is both an Open Model Factory distribution and a workspace for
building models with it. Preserve its central promise: one clone can take
arbitrary model and data code from local experimentation through governed,
reproducible training, evaluation, release, and deployment without depending on
a proprietary control plane.

This uppercase root file follows the [AGENTS.md standard](https://agents.md/)
and applies to the whole repository. A nearer nested `AGENTS.md` takes
precedence for conflicting instructions in its subtree; retain all
non-conflicting guidance from this file.

## Read the system in this order

1. `SPEC.md` is the normative product and conformance contract.
2. `factory/omf/schemas/` defines versioned resource and wire contracts.
3. `factory/omf/` and `tests/` are the executable implementation and evidence.
4. `README.md` and `docs/` explain workflows and operational guidance.

If these disagree, do not paper over the conflict in documentation. Restore the
implementation and schemas to the specification, or explicitly revise the
specification and compatibility contract.

## Orient before acting

When operating a bootstrapped factory, prefer its bounded machine interface to
inferring state from files or unbounded logs:

```sh
omf --output json agent context
omf --output json agent capabilities
omf --output json executor list
omf --output json executor preflight <binding> --workload <workload>
```

Use goals for explicit intent and budgets. Record durable knowledge only as an
evidence-backed claim, and supersede incorrect knowledge rather than rewriting
history. Refresh context before retrying a compare-and-set conflict. See
`docs/agent-control.md` for the projection, cursor, action, goal, and knowledge
contracts.

## Non-negotiable invariants

- Remain neutral to model architecture, modality, framework, language, hardware,
  scheduler, cloud, and storage provider. Core code must not assume tokens,
  messages, images, or fixed tensor shapes.
- Keep scientific intent in `WorkloadSpec` and physical placement, resource,
  transport, and provider configuration in `Binding`. Rebinding must not require
  workload or module changes.
- Preserve immutable resource revisions, content-addressed payload identity,
  attributable signed events, and bidirectional lineage. Mutable status must use
  the established compare-and-set or transition guard.
- Resolve executors exactly and fail before allocation when a provider is
  unknown, unready, or missing a required capability. Never silently fall back
  to local execution. Scheduler submission alone is not complete module
  transport.
- Treat generated/model actions and external data as untrusted. Do not bypass
  rights, isolation, vulnerability, policy, promotion, approval, budget, or
  separation-of-duties gates.
- Never put credentials, private keys, tokens, signed URLs, raw sensitive
  samples, prompts, model payloads, or operation/event payloads in Git, logs,
  errors, agent context, goals, or knowledge. Refer to governed artifacts by
  identity and digest.
- Do not claim cluster, federation, air-gap, scale, security, or frontier
  conformance from code paths, configuration, or scheduler acceptance. Claims
  require the measured, signed scenario evidence defined by `SPEC.md`.
- Git holds desired state and code; selected artifact stores hold data,
  checkpoints, model packages, and releases. `.omf/` is untracked local runtime
  state: never edit or commit it, and preserve metadata and identity through the
  documented backup process when their history must survive.

## Ownership map

- `factory/omf/factory.py`: application orchestration and lifecycle boundary.
- `factory/omf/agent.py`: bounded situation projection, action catalog,
  recommendations, goals, and accumulated knowledge.
- `factory/omf/cli.py` and `factory/omf/api.py`: two attributable interfaces to
  the same domain behavior; do not create interface-specific semantics.
- `factory/omf/schemas/`, `models.py`, and `schema_registry.py`: resource and
  validation contracts.
- `factory/omf/executors/`: provider registry plus execution lifecycle,
  transport, isolation, status, cancellation, logs, and restart attachment.
- `database.py`, `events.py`, `lineage.py`, and `operations.py`: durable state,
  audit evidence, derivation, and long-running-operation records.
- `artifacts.py`, `data.py`, `sync.py`, and `stores/`: immutable payload identity,
  registration, transfer, and storage.
- `evaluation.py`, `policy.py`, `releases.py`, and `deployments.py`: evidence and
  governed progression toward serving.
- `install.sh`, `factory/omf/install_support.py`, and `templates/project/`:
  non-destructive directory installation and the managed agent operator guide
  installed into project workspaces.
- `modules/`: user code and stable module protocol implementations.
- `workloads/`: portable scientific DAGs; `bindings/`: physical execution;
  `policies/`: governance; `data/`: manifests and intentionally checked-in
  non-sensitive fixtures.

Put behavior at the narrowest owner that can enforce it consistently. Change a
source of truth instead of adding a one-use adapter or command-specific override.

## Changing contracts and providers

- Keep changes small and coherent. Preserve behavior during a refactor, verify
  it, and only then alter behavior.
- A resource or wire-contract change normally requires its JSON Schema,
  validation/model code, canonical round-trip and migration coverage, CLI/API
  behavior, and documentation to move together.
- A new agent-visible operation must retain CLI/API parity where applicable and
  describe authorization, preconditions, effects, planning support,
  idempotency, risk, and cost in the action catalog.
- Provider-specific options belong under `Binding.spec.config.executor`, never
  in `WorkloadSpec`. Provider discovery uses trusted `omf.executors` entry
  points; duplicate or invalid providers fail closed.
- A provider may advertise `omf.module/v1` only when exact admitted source,
  request/result, and declared artifact transport work end to end. Advertise
  network denial only when the provider enforces it.
- Current built-in truth: local is complete; Slurm module transport requires an
  explicit shared filesystem and cannot enforce network denial; Kubernetes is
  scheduler-lifecycle-only. Modal, Vast.ai, and complete site integrations are
  external provider targets. Read `docs/executors.md` before changing this
  boundary.

## Development and verification

Use Python 3.11 or newer. Install exactly locked dependencies and the editable
package with:

```sh
python3 -m pip install --only-binary=:all: --require-hashes -r requirements.lock
python3 -m pip install --only-binary=:all: --require-hashes -r requirements.build.lock
python3 -m pip install --no-build-isolation --no-deps -e .
```

While iterating, run the narrowest relevant test. Before completing a code
change, run:

```sh
make test-all
```

This enforces Ruff formatting/linting, strict mypy, the full pytest suite, and
at least 85% branch coverage. Run `make build` for packaging, dependency,
entry-point, schema-bundling, or distribution changes. Also inspect
`git diff --check` and the final diff. Verify provider changes in success,
preflight-failure, cancellation, restart/reconciliation, and no-fallback states
as applicable.

Do not commit `.omf/`, credentials, payload data, coverage/build output, or
unsigned benchmark claims. Do not push, publish, deploy, alter shared
infrastructure, or perform destructive external actions without explicit
authorization.
