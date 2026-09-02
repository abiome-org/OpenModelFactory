# 6. Create one candidate

**Status: Tested now**

New dataset snapshots, module revisions, workload revisions, and runs are
tested. Native `MixSpec` consumption and objective-specific training recipes are
extension boundaries.

## Declare one scientific change

Start from the committed baseline and state one primary intervention:

| Intervention | New immutable input |
| --- | --- |
| Add pretraining data | dataset snapshot and workload input reference |
| Change a data mixture | source snapshots plus sampler/module configuration |
| Change an objective | objective/trainer module package and workload revision |
| Add post-training | new stage modules and dependencies |
| Change only placement | binding revision; scientific workload remains unchanged |

Do not overwrite the baseline dataset name or module source and then reuse the
old claim. Create names such as `pretrain-baseline-v1` and
`pretrain-candidate-v1`, preserve both revisions, and let lineage show which run
used each.

For a data candidate:

1. Establish rights and contamination checks for the added source.
2. Materialize a new snapshot rather than mutating the baseline snapshot.
3. Copy the baseline workload to a candidate file.
4. Change only the intended dataset reference or declared mixture behavior.
5. Keep the frozen evaluator stage and binding unchanged when possible.
6. Validate modules, preflight, review the diff, and commit desired state.

For an objective or trainer candidate, change the role module and semantic
configuration while retaining the same data and evaluation protocol. If the
change also alters batch semantics, sampling, preprocessing, or inference, list
those as additional interventions rather than calling the experiment a
single-variable ablation.

## Candidate identity checklist

Before running, capture:

- the goal and hypothesis;
- baseline and candidate Git revisions;
- dataset names, resource revisions, rights, and manifest digests;
- workload diff and expected new workload digest;
- module package changes;
- unchanged evaluation protocol and holdout revision;
- budget and stopping rule.

If the evaluation protocol must change, first re-evaluate the baseline under the
new protocol. Do not compare a candidate on protocol B directly with a baseline
on protocol A.
