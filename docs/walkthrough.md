# Walkthrough

This page runs one complete loop on the starter project: a baseline, a
candidate, a comparison, and the release gate. The transcript below is
executed by the test suite against the checked-in example, so it stays
accurate.

## Baseline

Run this from an installed project or from this checkout after
`omf bootstrap`. Set `OMF_ACTOR` to the real operator identity.

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

omf --actor "$ACTOR" --output json runs list
omf --actor "$ACTOR" --output json runs status "$run_id"
omf --actor "$ACTOR" --output json lineage show \
  "run:$run_id/stage:train"
omf --actor "$ACTOR" --output json evaluate "run/$run_id"

printf 'manual lifecycle completed: run/%s\n' "$run_id"
```

The run reaches `Succeeded`, its lineage links the dataset and the captured
module to the training stage, and `evaluate` records an `EvaluationResult`
whose `scores.passed` is true. That result is mechanics, not model quality:
the example is a two-parameter affine fit.

## Candidate

Change one thing and run again. The test suite does exactly this
with the training step count:

1. Edit `workloads/example-from-scratch.yaml` (for example raise `steps`).
2. `omf run workloads/example-from-scratch.yaml` and `omf evaluate run/<id>`.

The default policy archives uncommitted edits with the run. A site that explicitly
sets `dirtyWorktree: deny` requires a commit first.

Then compare on the metric the evaluation spec declared:

```sh
omf --actor research-agent experiment create longer-training \
  --baseline run/<baseline-run-id> --candidate run/<candidate-run-id> \
  --metric training-loss --direction minimize
```

The experiment refuses two results evaluated under different evaluation
revisions and records `candidate`, `baseline`, or `tie`. Record the outcome:

```sh
omf --actor research-agent knowledge record longer-training-result \
  --category observation \
  --claim "500 steps lowered training loss under the fixed protocol" \
  --confidence 0.95 --evidence experiment/longer-training
```

Update `MODEL_CARD.md` with the decision and the experiment revision.

## Refining from a release

A stage input may name `release/<name>`, `checkpoint/<name>`, or an artifact
digest instead of starting from initialization:

```yaml
stages:
  - name: refine
    module: modules/examples/affine-regression/module.yaml
    inputs: {base: release/affine-v1, dataset: dataset/example-affine}
    outputs: [modelState, loss, model, checkpoint]
```

The module reads `inputs.base.path` and `inputs.base.state`; lineage from the
new run leads back to the release it refined.

## Release gate

Promotion without evidence is denied, and the test suite asserts it:

```sh
omf --actor release-operator release create run/<run-id> \
  --name affine-v1 --intended-use "demo" --promote --approval reviewer
```

fails with `promotion denied by gates` because no vulnerability report covers
the model and module digests. `omf release evidence run/<run-id>` prints the
subjects a scanner must cover; the [releases page](releases.md) describes the
rest of the gate and the deployment forms.
