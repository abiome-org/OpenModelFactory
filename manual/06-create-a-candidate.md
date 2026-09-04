# 6. Create one candidate

**Status: Tested now**

New dataset snapshots, module revisions, workload revisions, and runs are
tested. Objective-specific training recipes are an extension boundary.

## Declare one experiment change

Start from the committed baseline and state one primary intervention:

| Intervention | New immutable input |
| --- | --- |
| Add pretraining data | dataset snapshot and workload input reference |
| Change a data mixture | source snapshots plus module configuration |
| Change an objective | objective/trainer module package and workload revision |
| Add post-training | new stage modules and dependencies |
| Change only placement | binding revision; workload behavior remains unchanged |

Do not overwrite the baseline dataset name or module source and then reuse the
old claim. Create names such as `pretrain-baseline-v1` and
`pretrain-candidate-v1`, preserve both revisions, and let lineage show which run
used each.

For a data candidate:

1. Establish rights and contamination checks for the added source.
2. Materialize a new snapshot rather than mutating the baseline snapshot.
3. Copy the baseline workload to a candidate file.
4. Change only the intended dataset reference or declared mixture behavior.
5. Keep the fixed evaluator stage and binding unchanged when possible.
6. Validate modules, preflight, review the diff, and commit the versioned
   project configuration.

For an objective or trainer candidate, change the role module and semantic
configuration while retaining the same data and evaluation protocol. If the
change also alters batch semantics, sampling, preprocessing, or inference, list
those as additional interventions rather than calling the experiment a
single-variable ablation.

## Refine from a prior release or checkpoint

A candidate may continue from evidence an earlier run produced instead of
starting from initialization. A stage input may name any of:

| Input value | What the module receives |
| --- | --- |
| `release/<name>` | the release's model artifact path, its state, and the model package reference |
| `checkpoint/<name>` | the checkpoint's module-state artifact path and its protocol state |
| `sha256:<digest>` | the restored payload of that artifact manifest |

```yaml
stages:
  - name: refine
    module: modules/examples/affine-regression/module.yaml
    operation: run
    inputs:
      base: release/affine-v1
      dataset: dataset/example-affine
    outputs: [modelState, loss, model, checkpoint]
```

Each reference is pinned and verified before the run is allocated, restored
under the stage's `inputs/` directory, and recorded as a `used` lineage edge
from the release, checkpoint, or artifact to the consuming stage. The module
reads `inputs.base.path` for the payload and `inputs.base.state` for the
protocol state when the source published one. Lineage from the new run
therefore leads back to the release it refined, and a downstream impact query
from that release lists every refinement built on it.

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
