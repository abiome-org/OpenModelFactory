# Evaluation

Evaluation turns a run into immutable evidence: metric thresholds from an
`EvaluationSpec`, compatibility vectors from a `ModelPackage`, and boolean
pass outputs from evaluator stages combine into one `EvaluationResult`.

## Model packages

```yaml
apiVersion: omf.dev/v1alpha1
kind: ModelPackage
metadata:
  name: example-affine
spec:
  signatures:
    input: {type: object, required: [input], properties: {input: {type: number}}}
    output: {type: object, required: [prediction], properties: {prediction: {type: number}}}
    state: {type: object, required: [slope, intercept]}
  adapters:
    trainingReference: {stage: train, operation: run, config: {action: train}}
    inferenceReference:
      module: modules/examples/affine-serving/module.yaml
      operation: run
      stateOutput: train.modelState
      config: {}
  compatibilityVectors:
    - name: positive
      method: predict
      inputs: {input: 3.0}
      expected: {prediction: 7.0}
      tolerances: {prediction: {absolute: 0.01, relative: 0.001}}
```

`signatures` are the input, output, and state contracts of the served model.
`trainingReference` must match a workload stage's operation and configuration;
`inferenceReference` names an independent serving module and the stage output
that carries the trained state. Compatibility vectors are inputs and expected
outputs, with optional finite non-negative tolerances and a seed. Both modules
are captured at admission, so the compatibility check later runs the exact
admitted serving module against the trained state; a training implementation
cannot vouch for its own serving path.

## Evaluation specs

```yaml
apiVersion: omf.dev/v1alpha1
kind: EvaluationSpec
metadata:
  name: example-affine
spec:
  metrics:
    - {name: training-loss, output: train.loss, maximum: 0.000001}
    - {name: parameter-check, output: evaluate.passed, minimum: 1.0}
```

Each metric names a run output and an optional `minimum` or `maximum`. Metric
names must be unique and may not be `passed` or `compatibilityPassed`. Commit
the spec before observing results; a changed spec is a new revision, and
results under different revisions are not directly comparable.

## Evaluating a run

```sh
omf --actor research-agent evaluate run/<run-id>
omf resource list --kind EvaluationResult
```

`evaluate` reads the run result, applies every metric threshold, runs the
compatibility vectors through the admitted serving module, collects every
boolean output named `*.passed`, and publishes `EvaluationResult`
`evaluation-<run-id>` with `scores` (each metric, `passed`, and
`compatibilityPassed`), `failures`, and the exact evaluation and model package
revisions it used. A run without a model package passes compatibility only
when an evaluator stage emits `compatibilityPassed: true` itself.

## Experiments

```sh
omf --actor research-agent experiment create longer-training \
  --baseline run/<baseline-run-id> --candidate run/<candidate-run-id> \
  --metric training-loss --direction minimize
```

An `Experiment` compares one numeric metric between two evaluation results
that used the same evaluation revisions and records `baseline`, `candidate`,
or `tie` with the delta. References may be `run/<id>`, an evaluation result
name, or a full `omf://` URI. Statistical treatment, repeats, slices, and
uncertainty belong to evaluator modules and their artifacts; the experiment
decision is only as strong as the metric behind it. Record the conclusion with
`omf knowledge record` and cite the experiment revision in the model card.
