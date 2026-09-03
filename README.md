# Open Model Factory

Open Model Factory (OMF) is a repository-centered system for developing models
without a proprietary control plane. It supports both greenfield and existing
projects: begin with a living model card, add model and data code, run repeatable
training and evaluation, benchmark each candidate against known baselines, then
release and deploy with the evidence that produced it.

OMF does not prescribe a model architecture, framework, modality, training
method, hardware platform, scheduler, cloud, or storage provider.

## Product loop

1. Write the intended use, interface, risks, and success measures in
   `MODEL_CARD.md`.
2. Implement model, data, training, and evaluation behavior as replaceable
   modules.
3. Describe what should run in a workload and where it should run in a binding.
4. Run, evaluate, and compare an immutable candidate with its baseline.
5. Repeat that cycle in continuous integration as code, data, and ideas change.
6. Promote a reviewed candidate to a signed release, deploy it, and feed
   operational findings into the next explicit iteration.

The model card records intent for people; versioned resources and tests enforce
machine behavior. OMF keeps the two connected without inventing a model-card
schema.

## Status

OMF is **1.0 Stable** for the repository-centered local lifecycle on CPython
3.11 and 3.12 on Linux x86-64, and for executor plugins implementing
`omf.executor/v1`. The built-in Slurm and Kubernetes providers are preview
lifecycle adapters, not supported remote workload paths. OMF makes no general
claim of production scale, cluster recovery, full environment reproduction, or
air-gap operation. See the [roadmap](ROADMAP.md) for the tested 1.0 boundary and
the [executor guide](docs/executors.md) for provider limits.

## Start a project

After cloning this distribution, inspect the non-destructive installation plan:

```sh
./install.sh --plan /path/to/model-project
./install.sh /path/to/model-project
. /path/to/model-project/.venv/bin/activate
```

The installer requires Python 3.11 or 3.12. It preserves existing files and
creates missing project scaffolding, including `MODEL_CARD.md`, an operator
`AGENTS.md`, a local binding, and a default policy. Start by completing the
model card, then inspect the factory:

```sh
omf --project /path/to/model-project --output json doctor
omf --project /path/to/model-project --output json agent context
```

The [operations runbook](docs/operations.md) describes exact installer effects,
manual installation, services, backup and restore, artifact stores, and offline
operation.

## Try the checked-in example

From an initialized checkout:

```sh
omf module validate modules/examples/affine-regression/module.yaml
omf module test modules/examples/affine-regression/module.yaml
omf data add data/fixtures/affine.jsonl --name example-affine --mode copy \
  --rights data/fixtures/rights.yaml
omf resource apply model-packages/example-affine.yaml
omf resource apply evaluations/example-affine.yaml
omf resource apply mixes/example-affine.yaml
omf executor preflight bindings/local.yaml \
  --workload workloads/example-from-scratch.yaml
omf run workloads/example-from-scratch.yaml --binding bindings/local.yaml
omf evaluate run/<run-id>
```

The [model-building manual](manual/README.md) owns the complete tested workflow,
including baseline/candidate comparisons and the conditional release path.

## Repository map

| Path | Role |
| --- | --- |
| `modules/` | Model, trainer, evaluator, data, and other executable components |
| `data/` | Versioned manifests and intentionally checked-in fixtures |
| `model-packages/` | Model interfaces, adapters, compatibility vectors, and provenance |
| `workloads/` | Portable stage graphs describing what runs |
| `bindings/` | Executors, resources, placement, and provider configuration |
| `evaluations/`, `mixes/` | Benchmark definitions and data-mixture intent |
| `policies/`, `deployments/` | Promotion rules and serving intent |
| `factory/omf/` | CLI, API, orchestration, storage, execution, and governance runtime |
| `tests/` | Product guarantees and release evidence |

Git stores code and versioned project configuration. Artifact stores hold data,
checkpoints, model payloads, and releases. Untracked `.omf/` holds local runtime
metadata, identity, and logs. The [architecture guide](docs/architecture.md)
maps the runtime modules and lifecycle boundaries in more detail.

## Documentation

- [Model-building manual](manual/README.md): the tested development loop.
- [Operations runbook](docs/operations.md): installation and operation.
- [Architecture](docs/architecture.md): lifecycle, state, and code ownership.
- [Executor guide](docs/executors.md): provider capabilities and limitations.
- [Agent guide](docs/agent-control.md): bounded status, goals, and actions.
- [Roadmap](ROADMAP.md): 1.0 evidence and post-1.0 direction.

## Development

Follow [CONTRIBUTING.md](CONTRIBUTING.md) and [`AGENTS.md`](AGENTS.md). Before
completing a code change, run:

```sh
make test-all
```

Run `make build` as well for packaging, dependency, entry-point, schema, or
distribution changes.
