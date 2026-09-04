# Open Model Factory

Open Model Factory (OMF) is a repository-centered system for developing models.
One clone takes a project from a model card through repeated training,
evaluation, and comparison to a signed release and a local deployment, and
keeps the evidence that produced each result immutable. There is no proprietary
control plane and no assumption about model architecture, framework, modality,
training method, hardware, scheduler, cloud, or storage provider.

## Status

OMF is **1.0 Stable** for the local lifecycle on CPython 3.11 and 3.12 on
Linux x86-64 and for executor plugins implementing `omf.executor/v1`. Only the
local executor is built in. OMF makes no claim of production scale, cluster
recovery, or air-gap operation; the [roadmap](ROADMAP.md) records the tested
boundary.

## Install

```sh
./install.sh --plan /path/to/model-project
./install.sh /path/to/model-project
. /path/to/model-project/.venv/bin/activate
```

The installer needs Python 3.11 or 3.12 and Git. It creates `omf.yaml`, a
local binding, a default policy, `MODEL_CARD.md`, an operator `AGENTS.md`, and
the runnable starter example, initializes Git, commits the project, and
bootstraps `.omf/`. Existing files are preserved. The
[operations page](docs/operations.md) covers manual installation, the HTTP
service, and backups.

## First loop

Every new project ships with a working example: an affine model trained and
evaluated by [`modules/examples/affine-regression`](modules/examples/affine-regression/main.py)
with a separate serving module. Run it before changing anything:

```sh
omf data add data/fixtures/affine.jsonl --name example-affine --mode copy \
  --rights data/fixtures/rights.yaml
omf resource apply model-packages/example-affine.yaml
omf resource apply evaluations/example-affine.yaml
omf run workloads/example-from-scratch.yaml
omf runs list
omf evaluate run/<run-id>
```

Then replace the example with the real model: write the module, point the
workload at it, and keep the same loop. The [walkthrough](docs/walkthrough.md)
follows a baseline and a candidate through comparison and release.

## Concepts

| Page | What it covers |
| --- | --- |
| [Projects](docs/projects.md) | `omf.yaml`, `.omf/`, namespaces, actors, policies, the model card |
| [Modules](docs/modules.md) | The `omf.module/v1` protocol, manifests, dependency locks, fixtures |
| [Data](docs/data.md) | Dataset snapshots, rights, verification, stores, sync |
| [Workloads](docs/workloads.md) | Stage graphs, bindings, runs, recovery |
| [Evaluation](docs/evaluation.md) | Model packages, evaluation specs, results, experiments |
| [Releases](docs/releases.md) | Evidence, promotion gates, aliases, deployments, serving |
| [Executors](docs/executors.md) | Provider capabilities and the plugin API |
| [Agent control](docs/agent-control.md) | Bounded context, goals, knowledge, the action catalog |
| [Operations](docs/operations.md) | Installation, service, backup and restore, distribution releases |
| [Architecture](docs/architecture.md) | Code map, lifecycle, invariants |

## Repository map

| Path | Role |
| --- | --- |
| `modules/` | Executable components behind `omf.module/v1` |
| `data/fixtures/` | Checked-in example data and rights |
| `model-packages/`, `evaluations/` | Model interfaces and metric thresholds |
| `workloads/`, `bindings/` | What runs and where it runs |
| `policies/`, `deployments/` | Authorization rules and serving intent |
| `factory/omf/` | The runtime: CLI, API, orchestration, storage, execution |
| `tests/` | Product guarantees |

Git holds code and versioned configuration. Artifact stores hold data,
checkpoints, models, and releases. `.omf/` holds untracked local state.

## Development

Follow [CONTRIBUTING.md](CONTRIBUTING.md) and [`AGENTS.md`](AGENTS.md). Run
`make test-all` before completing a change and `make build` for packaging
changes. `make hooks` installs the lint pre-commit hook.
