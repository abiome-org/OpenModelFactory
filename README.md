# Open Model Factory

Open Model Factory runs model experiments from ordinary training and evaluation
scripts. Define the task, candidates, metrics, and limits in one YAML file. OMF
captures source and data, runs the work, compares results, and exports the model
with its evidence. Your agent chooses what to try next.

## Try a real model

On Linux with Python 3.11 or 3.12 and unprivileged user namespaces:

```sh
make setup
make example
```

This downloads the public UCI SMS corpus, trains three scikit-learn classifiers,
interrupts and recovers a controller, selects a candidate, reproduces its scores,
and tests exported inference in a fresh environment. Open
`.venv/text-classification/review.html` for the comparison and `results.json`
for timings, compute, and reproduction evidence. Use `EXAMPLE_DIR=/new/path`
to repeat it. The [example definition](examples/text-classification/experiment.yaml)
and [model card](examples/text-classification/MODEL_CARD.md) describe the experiment.

## Bring your scripts

Install OMF into your environment, then run from your model project:

```sh
omf experiment init --name my-model --objective "Improve useful task performance" --source src
# Edit experiment.yaml to name your scripts, data, outputs, metrics, and candidates.
omf experiment run experiment.yaml --candidate baseline
omf experiment run experiment.yaml --candidate candidate --detach
omf experiment list
omf experiment status <run-id>
omf experiment review <run-id> --baseline <baseline-id> --html review.html
omf experiment reproduce <run-id>
omf experiment export <run-id> --to model
```

Commands are argument lists without a shell. Use `{inputs[name]}` for data or
model paths, `{parameters[name]}` for candidate settings, and `{output}` for the
output directory; double literal braces. Each script's optional `inputs` selects
which datasets or training artifacts it receives. Scripts need no OMF imports.
Evaluation writes a JSON object of finite numeric metrics and explicit boolean
`passed` and `compatibilityPassed` checks. Optional examples are JSON records with
stable `id`, `input`, `expected`, `prediction`, and numeric `score` fields.

Runs capture uncommitted source by default and respect Git ignore rules.
Review prints a compact comparison; `--details` includes examples, diffs, and
runtime evidence. `dependencies` names a hash-pinned
binary requirements lock inside each script's source directory. Separate training
and evaluation source directories when their code changes independently: changes
to the evaluator's captured source, inputs, or protocol flag comparisons for review.
Use results to improve training and selection; report reused evaluation data as
development evidence. `omf experiment schema` exposes the full definition schema.

Runs survive agent sessions. Use `omf operation reconcile <run-id>` to resume and
`omf operation cancel <run-id> --reason "Try another candidate"` to stop. Each stage
has a wall timeout; local CPU, memory, process, and file-size limits use POSIX
process limits. Measured wall/CPU time is reported separately from configured
limits. Monetary cost is currently unmeasured.

For MLflow, install `open-model-factory[tracking]`, then run
`omf experiment track <run-id> --uri sqlite:///tracking.db` or use your tracking
server URI. Repeated exports reuse the same MLflow run. CLI and authenticated
HTTP expose the same workflow; `omf agent capabilities experiment.run` describes it.

## Releases and deployment

The [walkthrough](docs/walkthrough.md) covers the lower-level workload, comparison,
signed release, and local serving lifecycle. `./install.sh /path/to/model-project`
installs its runnable starter and operator guide. Existing files are preserved.
Generated experiment manifests remain inspectable under `.omf/experiments/`.

OMF 1.0 supports the local lifecycle on CPython 3.11/3.12 on Linux x86-64 and
the `omf.executor/v1` plugin contract. Only local execution is built in. Production
scale, cluster recovery, and full air-gap operation require deployment-specific
evidence. See [operations](docs/operations.md) for installation and support policy.

## Reference

| Page | What it covers |
| --- | --- |
| [Projects](docs/projects.md) | Configuration, namespaces, actors, policies |
| [Modules](docs/modules.md) | Protocol, manifests, dependency locks, fixtures |
| [Data](docs/data.md) | Snapshots, rights, verification, stores, sync |
| [Workloads](docs/workloads.md) | Stage graphs, bindings, runs, recovery |
| [Evaluation](docs/evaluation.md) | Metrics, compatibility, comparisons |
| [Releases](docs/releases.md) | Promotion, aliases, deployments, serving |
| [Executors](docs/executors.md) | Provider capabilities and plugin API |
| [Agent control](docs/agent-control.md) | Context, goals, knowledge, action catalog |
| [Architecture](docs/architecture.md) | Code ownership and invariants |

Git holds code and configuration. Artifact stores hold data and models. `.omf/`
holds untracked runtime state. For development, use `make check`,
`make test TEST_ARGS='tests/test_experiments.py -q'`, and `make test-all`.
See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).
