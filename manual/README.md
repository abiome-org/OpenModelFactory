# Open Model Factory manual

**Status: Tested now**

This manual is the task-oriented path from a clean project to an attributable
model-development decision. It complements the normative [specification](../SPEC.md),
the concise [project overview](../README.md), and the operational references in
[`docs/`](../docs/operations.md). When prose conflicts with the specification or
the executable schemas, the specification and schemas win.

## Status labels

Every chapter declares one of these statuses:

- **Tested now** — the documented path is exercised against the current local
  CLI and checked-in example.
- **Conditional** — the path works only with named external infrastructure,
  evidence, or independent authorization.
- **Extension blueprint** — the contracts exist, but the repository does not
  yet provide the claimed end-to-end workflow. Blueprints contain no invented
  commands.

The example is intentionally a tiny from-scratch affine model implemented with
the Python standard library. It proves model-package and MixSpec admission,
training, checkpoint publication, evaluation, and experiment mechanics—not mix
delivery/replay, model quality, benchmark coverage, RLVR readiness, or scale.
Framework- and modality-specific conveniences belong in optional starter packs,
not factory core.

## Learning path

1. [Operate a project](01-operate-a-project.md).
2. [Build a module](02-build-a-module.md).
3. [Bring and partition data](03-bring-and-partition-data.md).
4. [Design evaluation before training](04-design-evaluation.md).
5. [Train and measure a baseline](05-train-and-measure-a-baseline.md).
6. [Create one candidate](06-create-a-candidate.md).
7. [Run a controlled experiment](07-run-a-controlled-experiment.md).
8. [Design RLVR post-training](08-add-rlvr-post-training.md).
9. [Rebind execution](09-rebind-execution.md).
10. [Release and deploy](10-release-and-deploy.md).

## Global integrity rules

- Freeze evaluation data, preprocessing, inference policy, metrics, seeds,
  thresholds, slices, and decision rules before comparing candidates. Any
  change creates a new protocol revision and breaks direct comparability.
- Keep training data, development data, reward tasks, reward verifiers, and
  independent evaluation holdouts as distinct immutable assets with appropriate
  access boundaries.
- A metric, task, or verifier used as a training reward is compromised for
  independent evaluation. Never tune repeatedly on the final holdout.
- Keep hidden tests, verifier logic, credentials, prompts, sensitive samples,
  and signed URLs out of actor inputs, Git, logs, events, and knowledge claims.
- A scheduler accepting a job does not prove source transport, result
  retrieval, artifact completeness, recovery, security, scale, or conformance.
- Never claim cluster, federation, air-gap, security, scale, or frontier
  conformance without the measured and signed scenario evidence required by
  the specification.

## Canonical tested local lifecycle

Run this once from a clean clone after installing OMF. The commands use only
the checked-in fixture, example module, workload, and local binding. Set
`OMF_ACTOR` to the real attributable operator identity; the scaffold default is
used below only for the local example. This is the only shell transcript marked
for execution by the manual test.

<!-- manual-test: local-lifecycle -->
```sh
set -euo pipefail

ACTOR="${OMF_ACTOR:-local-user}"

omf --actor "$ACTOR" --output json bootstrap
omf --actor "$ACTOR" --output json doctor
omf --actor "$ACTOR" --output json module validate \
  modules/examples/affine-regression/module.yaml
omf --actor "$ACTOR" --output json module test \
  modules/examples/affine-regression/module.yaml

omf --actor "$ACTOR" --output json data add \
  data/fixtures/affine.jsonl \
  --name example-affine \
  --mode copy \
  --rights data/fixtures/rights.yaml
omf --actor "$ACTOR" --output json data verify example-affine

omf --actor "$ACTOR" --output json resource apply \
  model-packages/example-affine.yaml
omf --actor "$ACTOR" --output json resource apply \
  evaluations/example-affine.yaml
omf --actor "$ACTOR" --output json resource apply \
  mixes/example-affine.yaml

omf --actor "$ACTOR" --output json store add secondary \
  --driver filesystem \
  --endpoint .omf/manual-secondary
omf --actor "$ACTOR" --output json sync push dataset/example-affine \
  --to secondary --plan
omf --actor "$ACTOR" --output json sync push dataset/example-affine \
  --to secondary

omf --actor "$ACTOR" --output json executor preflight \
  bindings/local.yaml \
  --workload workloads/example-from-scratch.yaml

run_json="$(omf --actor "$ACTOR" --output json run \
  workloads/example-from-scratch.yaml \
  --binding bindings/local.yaml)"
printf '%s\n' "$run_json"
run_id="$(printf '%s' "$run_json" | \
  python3 -c 'import json, sys; print(json.load(sys.stdin)["runId"])')"

omf --actor "$ACTOR" --output json runs status "$run_id"
omf --actor "$ACTOR" --output json lineage show \
  "run:$run_id/stage:train"
omf --actor "$ACTOR" --output json evaluate "run/$run_id"

printf 'manual lifecycle completed: run/%s\n' "$run_id"
```

Success means the run reaches `Succeeded`, lineage is non-empty, and the
example evaluator emits a boolean pass that `omf evaluate` records as immutable
`EvaluationResult` evidence. It does not mean a real benchmark suite ran.
