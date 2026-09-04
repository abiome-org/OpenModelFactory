# Open Model Factory contributor guide

This file is for contributors changing the Open Model Factory distribution. The
self-contained guide installed into projects is maintained separately in
`templates/project/AGENTS.md`; do not turn this contributor guide into an
operator runbook.

Preserve the product promise: one clone can take a greenfield or existing model
project from a living model card through repeated implementation, training,
evaluation, benchmarking, release, and deployment without requiring a
proprietary control plane.

This uppercase root file follows the [AGENTS.md standard](https://agents.md/)
and applies to the whole repository. A nearer nested `AGENTS.md` takes
precedence for conflicting instructions in its subtree. Retain all other
applicable guidance from this file.

## Sources of truth

Read and change the system in this order:

1. `factory/omf/schemas/`, `models.py`, and `schema_registry.py` define
   versioned resource and wire formats.
2. `factory/omf/` and `tests/` define executable behavior and its evidence.
3. `docs/architecture.md` explains system invariants and ownership boundaries.
4. `manual/` provides status-labeled, tested model-building workflows.
5. `README.md`, the rest of `docs/`, and `ROADMAP.md` explain orientation,
   operation, and planned maturity.

If these disagree, do not hide the conflict with a documentation-only change.
Restore the implementation to its versioned format, or update the format,
compatibility notes, implementation, tests, and documentation together.

## Required system behavior

- Keep core code neutral to model architecture, modality, framework, language,
  hardware, scheduler, cloud, and storage provider. It must not assume tokens,
  messages, images, or fixed tensor shapes.
- Keep what a workload does in `WorkloadSpec`. Keep placement, resources,
  transport, and provider configuration in `Binding`. Changing a binding must
  not require module or workload changes.
- Preserve immutable resource revisions, content-addressed payload identity,
  signed events tied to an actor, and bidirectional lineage. Mutable status must
  use the established compare-and-set or transition guard.
- Resolve an executor by its exact name. Return an error before allocating work
  when the provider is unknown, unready, or missing a required capability;
  never silently run the workload locally instead. Scheduler submission alone
  is not complete module transport.
- Treat generated actions, model actions, and external data as untrusted. Never
  bypass rights, isolation, vulnerability, policy, promotion, approval, budget,
  or separation-of-duties checks.
- Never put credentials, private keys, tokens, signed URLs, raw sensitive
  samples, prompts, model payloads, or operation/event payloads in Git, logs,
  errors, agent context, goals, or recorded findings. Refer to governed
  artifacts by identity and digest.
- Do not infer cluster, federation, air-gap, scale, security, or recovery
  behavior from code paths, configuration, provider names, or scheduler
  acceptance. Support only what direct tests and measurements demonstrate.
- Git holds code and versioned project configuration. Selected artifact stores
  hold data, checkpoints, model packages, and releases. `.omf/` is untracked
  local runtime state: never edit or commit it.

## Code ownership

- `factory/omf/factory.py`: application orchestration and lifecycle changes.
- `factory/omf/agent.py`: bounded status, action descriptions,
  recommendations, goals, and evidence-backed findings.
- `factory/omf/cli.py` and `factory/omf/api.py`: two attributed interfaces to
  the same application behavior; do not create interface-specific semantics.
- `factory/omf/schemas/`, `models.py`, and `schema_registry.py`: resource and
  validation formats.
- `factory/omf/executors/`: provider discovery, execution lifecycle, transport,
  isolation, status, cancellation, logs, and restart attachment.
- `database.py`, `events.py`, `lineage.py`, and `operations.py`: durable state,
  audit evidence, derivation, and long-running operation records.
- `artifacts.py`, `data.py`, `sync.py`, and `stores/`: payload identity,
  registration, transfer, and storage.
- `evaluation.py`, `policy.py`, `releases.py`, and `deployments.py`: measured
  evidence and governed progression toward serving.
- `install.sh`, `factory/omf/install_support.py`, and `templates/project/`:
  non-destructive installation and the installed operator guide.
- `manual/`: task-oriented, CI-verified guidance that distinguishes tested,
  conditional, and proposed paths.

Put behavior in the narrowest owner that can enforce it consistently. Change a
source of truth instead of adding a one-use adapter or command-specific
override.

## Changing formats, interfaces, and providers

- Keep changes small and coherent. When refactoring, preserve behavior and
  verify it before changing behavior.
- A resource or wire-format change normally requires its JSON Schema,
  validation/model code, round-trip and migration coverage, CLI/API behavior,
  compatibility notes, and documentation to change together.
- A new agent-visible operation must retain CLI/API parity where applicable and
  describe authorization, preconditions, effects, planning support,
  idempotency, risk, and cost in the action catalog.
- Provider-specific options belong under `Binding.spec.config.executor`, never
  in `WorkloadSpec`. Provider discovery uses trusted `omf.executors` entry
  points; duplicate or invalid providers must stop with an error.
- A provider may advertise `omf.module/v1` only when admitted source,
  request/result, and declared artifact transport work end to end. It may
  advertise network denial only when it enforces that isolation.
- Read `docs/executors.md` before changing executor capabilities. That guide is
  the source of truth for current built-in behavior and limitations.
- Keep CLI reference details with the CLI, operations in `docs/operations.md`,
  the example workflow in `manual/`, and release criteria in `ROADMAP.md`.
  Avoid copying detailed capability claims into overview documents.

## Engineering rules

These rules are not preferences. A change that violates one is not done.

- **No toothless unit tests of any kind, ever.** Integration tests and stress
  tests are permitted so long as they are realistic and never tautological. A
  test that cannot fail when the behavior it names is broken is worthless.
- **Never mock anything.** Always run the real full thing if possible. If the
  real thing cannot run in the test environment, the test is skipped with the
  reason recorded, not replaced by a fake.
- **Tautological tests are considered actively harmful.** Asserting that code
  returns what it was just handed, that a constant equals itself, or that a
  fake behaves like its script is worse than no test, because it manufactures
  confidence. This is stated twice on purpose.
- **Never keep something ceremonially.** A schema no code produces or consumes,
  a module nothing imports, a command that cannot complete its purpose, or a
  required manifest field nothing enforces is deleted, not documented.
- **Keep architecture maximally simple.** Prefer one obvious path over a
  configurable one. Add abstraction only when a second real use exists.
- **Deterministic linter rules that reduce cyclomatic complexity run as a hook
  frequently.** Ruff's `C901`, `PLR0911`, `PLR0912`, and `PLR0915` are enabled
  in `pyproject.toml`; `.claude/settings.json` runs them after every edit and
  `make hooks` installs the same check as a Git pre-commit hook.
- **No comments in the code.** The only documentation of code is
  `docs/architecture.md`. Docstrings and `#` comments are rejected by
  `make lint` (`tools/check_no_comments.py`); tool directives such as
  `# noqa` and `# type: ignore` are the only exception.
- **Before any PR, audit ruthlessly for cyclomatic complexity.** Run
  `make lint` and treat every complexity finding as a defect to refactor, not
  a threshold to raise.
- **Before any PR, review the whole change with a subagent** when the harness
  provides one, and ask it to ruthlessly reduce lines of code without losing
  functionality. Apply what survives your own reading.

## Development and verification

Use Python 3.11 or 3.12. Install the locked dependencies and editable package:

```sh
python3 -m pip install --only-binary=:all: --require-hashes -r requirements.lock
python3 -m pip install --only-binary=:all: --require-hashes -r requirements.build.lock
python3 -m pip install --no-build-isolation --no-deps -e .
```

Run the narrowest relevant test while iterating. Before completing any code
change, run `make test-all`; it checks Ruff formatting and linting, strict mypy,
the full pytest suite, and at least 85% branch coverage.

Run `make build` for packaging, dependency, entry-point, bundled-schema, or
distribution changes. Inspect `git diff --check` and the final diff. For
provider changes, test success, preflight errors, cancellation,
restart/reconciliation, and no-local-fallback behavior as applicable.

Do not commit `.omf/`, credentials, payload data, coverage/build output, or
unsigned benchmark claims. Do not push, publish, deploy, alter shared
infrastructure, or perform destructive external actions without explicit
authorization.
