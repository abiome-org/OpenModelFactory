# Changelog

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
