# 4. Design evaluation before training

**Status: Tested now**

Evaluator stages and boolean pass evidence are tested. Full `EvaluationSpec`
suite scheduling through the CLI is an extension blueprint.

## Freeze the decision protocol

Before running a baseline, commit a protocol that identifies:

- immutable benchmark or task data and its access boundary;
- preprocessing, prompt/template, tools, inference, and decoding policy;
- metric and verifier implementations;
- seeds, repeats, resource limits, slices, and invalid-case handling;
- contamination checks and known benchmark exposure;
- thresholds and the decision rule you will apply after results exist.

If any item changes, create a new evaluation revision. Results from different
revisions may be informative, but they are not a controlled direct comparison.

## Current executable pattern

The [example workload](../workloads/example-statistical.yaml) places an
`evaluate` stage after `train`. The evaluator returns a boolean output named
`evaluate.passed` and an evaluation artifact. After the run, this command
materializes immutable evidence from boolean outputs ending in `.passed` or
`.pass`:

```sh
omf --actor research-agent --output json evaluate run/<run-id>
```

This command does **not** execute the normative `EvaluationSpec` as an external
benchmark suite. The richer in-process evaluation primitive can retain repeats,
distributions, confidence intervals, slices, failures, and resource usage, but
it is not the same CLI path. A production evaluator module should emit those
details as an immutable artifact and expose an unambiguous pass result for the
current release gate.

## Package a benchmark responsibly

Treat benchmark data as a governed dataset snapshot and the harness, metrics,
and verifier as versioned modules. For an external benchmark, record upstream
revision, local modifications, rights, and differential validation against the
upstream harness. Report distributions, invalid cases, timeouts, uncertainty,
and resource use—not only a mean or pass bit.

Hidden holdouts and verifier secrets require separate authorization from the
training actor. Never place them in prompts, module inputs visible to the actor,
Git, logs, or traces.

## Reward and benchmark separation

A task, metric, or verifier used to select data, tune prompts, shape reward, or
train a policy is development evidence. It is no longer an independent final
evaluation. RLVR reward logic and independent evaluation must use distinct
assets and access controls, even if they share an environment interface.

## Evidence before the next chapter

- The evaluation protocol is committed before baseline results are observed.
- Holdout access is separated from training and reward actors.
- Thresholds and comparison rules are explicit.
- Known contamination and benchmark exposure are recorded.
