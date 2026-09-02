# 7. Run a controlled experiment

**Status: Tested now**

OMF can execute and retain two immutable runs and compare one numeric metric
when both `EvaluationResult` resources identify the same exact evaluation
revisions. Rich statistical decisions remain evaluator- or analysis-module-owned.

## Lock the comparison set

Before either run, record the fields that must remain equal: evaluation data and
verifier, preprocessing, inference policy, seeds, thresholds, binding when
feasible, and all scientific inputs outside the declared intervention. Record
expected numerical nondeterminism and the number of repeats needed for the
decision.

Run baseline and candidate as separate admitted workloads. For each run inspect:

```sh
omf --output json runs status <run-id>
omf --output json lineage show run:<run-id>/stage:<stage>
omf --actor research-agent --output json evaluate run/<run-id>
omf --output json resource list --kind EvaluationResult
```

## Build the comparison

Copy the exact baseline and candidate `EvaluationResult` URIs from `resource
list`, then apply the predeclared metric direction:

```sh
omf --actor research-agent --output json experiment create candidate-v1 \
  --baseline 'omf://<namespace>/evaluationresult/<baseline>@<revision>' \
  --candidate 'omf://<namespace>/evaluationresult/<candidate>@<revision>' \
  --metric <metric-name> --direction minimize
```

The command rejects results admitted under different evaluation revisions. It
does not infer significance, equivalence margins, or uncertainty policy.

Use immutable outputs to populate a table such as:

| Field | Baseline | Candidate |
| --- | --- | --- |
| Dataset/resource revisions | exact refs | exact refs |
| Workload and module digests | exact digests | exact digests |
| Binding digest | exact digest | exact digest |
| Evaluation protocol revision | same revision | same revision |
| Quantitative metrics and uncertainty | evaluator artifact | evaluator artifact |
| Invalid cases and failures | retained | retained |
| Resource observations | retained | retained |
| Run and evaluation revisions | exact refs | exact refs |

The current `omf evaluate` result records boolean evaluator outputs. Put
quantitative distributions, uncertainty, slices, and resource measurements in
the evaluator artifact or a separately versioned analysis module. Do not derive
a broad claim from the pass bit alone.

## Make an attributable decision

Apply the decision rule frozen in chapter 4. Report regressions and invalid
cases as prominently as improvements. If evidence supports a bounded claim,
record it without rewriting previous knowledge:

```sh
omf --actor research-agent --output json knowledge record candidate-result \
  --category observation \
  --claim "candidate improved the frozen protocol within the declared scope" \
  --confidence 0.95 \
  --evidence evaluation/<candidate-evaluation> \
  --run-id <candidate-run-id> \
  --goal-ref goal/improve-quality
```

If the result is inconclusive, retain that result and design a new experiment.
Changing the holdout, threshold, or analysis after seeing results requires a new
protocol revision, not a retroactive reinterpretation.
