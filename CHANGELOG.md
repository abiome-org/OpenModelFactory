# Changelog

## Unreleased

- Use the configured project owner for local commands, honor explicit project
  directories, archive uncommitted work by default, and summarize reviews unless
  details are requested. Capture a standalone script adapter and distinguish
  inherited Python environments in the dependency cache.
- Check dataset rights according to training or evaluation use, so held-out data
  can be evaluated without granting training permission. Preserve revocation and
  current-rights checks throughout admission, recovery, and promotion.
- Add executable experiments for ordinary scripts: captured source and data,
  named candidates, metric criteria, limits, review reports, reproduction,
  artifact/source export, and optional MLflow tracking through CLI and HTTP.
- Add durable run cancellation and recoverable evaluation publication; support
  directory model outputs and materialize artifacts between dependent stages.
- Ship a real scikit-learn classifier loop with controller interruption,
  reproduction, and fresh-environment inference. Remove completed planning docs.

- Encourage using evaluation feedback to improve training, rewards, and candidate
  selection. Replace blanket separation rules with accurate reporting of data
  use and independent measurement when needed.
- Share action contracts across CLI help, HTTP routes, authorization scopes,
  OpenAPI, and focused agent discovery; fill missing CLI/HTTP read interfaces.
- Separate evaluation, publishing, and deployment ownership while preserving
  public Factory methods and persisted resource formats.
- Preserve event continuity and progress under context byte limits; query
  operation metadata directly with correct totals and payload-free focus.
- Enforce project policy on goal status transitions, clarify metadata trust,
  and remove policy-bypass remediation and speculative workflow instructions.
- Use one locked local development setup for contributors and agents. Remove
  duplicate CI runs, the comment ban, and automatic edit hooks; retain complexity,
  types, coverage, and isolated offline installation tests.
- Accept secret input through stdin or a hidden prompt, reject nonfinite CLI
  budgets, and isolate CLI invocation state.
- Removed everything no run path used: the federation, capacity, telemetry,
  sampler, feedback, environment, inference, evaluation, and deployment-plan
  modules with their schemas; `MixSpec`; the preview Slurm and Kubernetes
  executors; checkpoint replay claims; `starter-packs/`; and the unread
  manifest fields of every kind. Database migration 6 drops the federation
  tables.
- Manifests declare only what the runtime enforces. `metadata.namespace` is
  optional and defaults to the project namespace. A `Binding` names its
  executor, optional POSIX limits under `spec.resources` (`cpuSeconds`,
  `addressSpaceBytes`, `processes`, `fileSizeBytes`, `timeoutSeconds`), and
  provider options directly under `spec.config`. A `Module` needs only
  `entryPoint` and `environment`; contracts default to open objects and a
  module without fixtures is tested with one `validate` request.
- `omf token`, `omf secret`, `omf backup`, and `omf restore` moved under
  `omf admin`. Table output prints aligned columns for lists and YAML for
  objects.
- Added `omf runs list`, `omf release list`, `omf release show`,
  `omf release evidence` (the vulnerability report skeleton a run needs),
  `omf deployment list`, and `omf module init`. Experiment references accept
  `run/<id>` and evaluation result names as well as URIs.
- `install.sh` copies the runnable starter example into a new project and
  makes the initial commit, so a fresh project can run a workload immediately.
- Cyclomatic-complexity limits and a no-comments rule are enforced by
  `make lint`, an editor hook, and `make hooks`; the tests no longer patch or
  fake any OMF code path.

- Both GitHub Actions workflows were invalid YAML: the unquoted
  `--only-binary=:all: --require-hashes` argument was parsed as a mapping, so
  every run failed before any job started. The lines are quoted and both
  workflows accept manual dispatch. Test jobs also lift the runner's
  AppArmor restriction on unprivileged user namespaces, which the local
  executor needs to enforce module network denial; without it every workload
  run on a hosted runner is refused as not ready.
- `make release-candidate` staged its build inside the checkout, so the
  release tool's own post-build source check always failed. Staging now
  happens outside the repository.
- The local executor launches a module interpreter through the path it named
  instead of the resolved symlink target, so modules run inside the project's
  virtual environment again. Projects created by `install.sh` were affected.
- The local executor realizes non-empty, hash-pinned dependency locks into
  cached virtual environments under `.omf/environments/`, with optional
  `dependencyWheelhouse` and `dependencyIndex` binding options.
- `omf module test` accepts `--binding` so fixtures run with the same executor
  and dependency options as the workload.
- Promoting a later release to an alias that already points at an earlier
  release now moves the alias under the current alias version instead of
  conflicting unconditionally.
- Policy documents in the project policy directory are loaded and enforced:
  rules authorize the acting identity for mutating actions, `dirtyWorktree`
  governs whether an uncommitted tree can admit a workload, and admission
  records the commit, worktree state, and policy digest. Unenforceable policy
  configuration is rejected at load time. Previously the scaffold policy was
  never read.
- A `service` deployment without a command now serves the release: the
  admitted inference adapter and model state are staged into the deployment
  directory and a local HTTP worker answers `POST /v1/infer` through the module
  protocol, with `GET /healthz` and an `endpoint` in the deployment status.
- Workload stage inputs may name `release/<name>`, `checkpoint/<name>`, or an
  artifact digest. The referenced artifacts are pinned and verified before a
  run is allocated, restored into the stage input directory with their
  protocol state, and linked to the consuming stage in lineage, so a
  refinement run derives from the release it started from.

## 1.0.0 — 2026-09-03

Open Model Factory 1.0 establishes the repository-centered model-development
loop: start or adopt a model card, admit model and data code, run and recover
portable workloads, compare measured candidates, and govern release and local
deployment without a proprietary control plane.

The release adds identity-preserving backup and restore, checksummed database
migrations, interrupted-run attachment without hidden replay, independent
training and serving compatibility, live data-rights checks, attributable
feedback approval, and the stable `omf.executor/v1` plugin API. Candidate builds
are reproducible and rehearsed from wheel and source archives with checksums,
SPDX SBOM, provenance, vulnerability review, and an external signing hook.

Existing `omf.dev/v1alpha1` resources remain accepted. Early model packages
whose inference reference points to a training stage remain readable, but they
must be revised to name an independent inference module before producing new
release evidence.
