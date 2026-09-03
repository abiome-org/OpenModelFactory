# Changelog

## Unreleased

- Both GitHub Actions workflows were invalid YAML: the unquoted
  `--only-binary=:all: --require-hashes` argument was parsed as a mapping, so
  every run failed before any job started. The lines are quoted and both
  workflows accept manual dispatch. Test jobs also lift the runner's
  AppArmor restriction on unprivileged user namespaces, which the local
  executor needs to enforce module network denial; without it every workload
  run on a hosted runner is refused as not ready.
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
