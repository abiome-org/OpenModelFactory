# Changelog

## 2.0.0 — unreleased

- Center the factory on captured data and recipes, durable runs, measured
  comparisons, and usable model versions. Remove goal/knowledge APIs, generic
  agent recommendations, static approval/risk labels, and unused break-glass
  and policy configuration machinery. Agent context reports factory state.
- Separate saving a release from selecting it. Add `release promote` for existing
  releases. Default selection requires passing evaluation; projects can configure
  compatibility and vulnerability requirements or allow failed evaluation.
  Current data rights, provenance, signatures, and atomic alias updates remain
  enforced. Pin deployments to exact releases and recheck requirements on
  rollback. Aliases work for deployment and refinement inputs. Remove
  caller-supplied approvals and manufactured release claims.
- Use `omf.release/v2` manifests and action catalog version 2. Existing runs,
  datasets, and artifacts remain intact. Recreate older releases from recorded
  runs before promotion/deployment. Remove legacy `unsignedModules`, `sync`, and
  `promotion.requireCompleteLineage` policy keys before upgrading.
- Add ordinary-script experiments with captured source/data, candidate review,
  reproduction, export, MLflow tracking, and durable cancellation. Ship a real
  classifier example that exercises recovery and fresh-environment inference.
- Default local commands to the configured owner, honor exact project paths,
  archive uncommitted edits, summarize reviews, and capture a standalone script
  adapter. Cache dependencies by the actual inherited Python environment.
- Check dataset rights by training/evaluation role through admission, execution,
  recovery, and promotion. Use evaluation feedback for development and report
  that use accurately.
- Consolidate CLI/HTTP contracts and locked development setup. Remove unused
  providers, schemas, planning documents, automatic hooks, and duplicate CI.
  Preserve type, complexity, coverage, and isolated installation checks.

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
