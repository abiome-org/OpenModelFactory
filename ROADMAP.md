# Open Model Factory roadmap

Open Model Factory develops models in their own repositories: a greenfield or
existing project moves from a living model card through repeated implementation,
training, evaluation, benchmark comparison, release, and deployment. Ordinary
tests carry the product guarantees.

## Path to 1.0

| Stage | Product result | Release evidence |
| --- | --- | --- |
| Alpha | One local model-building path and immutable evidence | Golden-path module, data, workload, evaluation, release, and deployment tests |
| Hardened Alpha | Repeatable greenfield iteration and durable state | Model-card baseline/candidate CI, migrations, backup/restore, and interrupted-run tests |
| Beta | Dependable extension and governance boundaries | Independently packaged executor acceptance tests, live data-rights denial, and separate feedback approval |
| Release candidate | Installable, recoverable distribution | Reproducible wheel/sdist builds; isolated install, upgrade, and backup/restore recovery |
| 1.0 Stable | Supported local product and stable executor API | Full release suite on the supported Python matrix plus checksums, SPDX SBOM, provenance, vulnerability review, and external signing |

These stages are complete in the 1.0 code and ordinary test suite. The supported
boundary is the local lifecycle on CPython 3.11 and 3.12 on Linux x86-64 and the
independently tested `omf.executor/v1` plugin interface. `omf.dev/v1alpha1`
resources remain accepted throughout 1.x; an incompatible resource format must
use a new `apiVersion` and provide an upgrade path.

## Compatibility and support

OMF follows semantic versioning for documented commands and HTTP operations,
persisted state, installed project templates, and `omf.executor/v1`. A breaking
change requires a major release. A deprecation remains available for at least
one minor release and is named in `CHANGELOG.md`; urgent security removal is the
exception and must include a migration or mitigation.

The newest 1.x minor receives correctness and security fixes. The preceding
minor receives security fixes for six months, and 1.x receives security fixes
for twelve months after 2.0. Release notes record exact end dates when a
successor starts either clock.

## Post-1.0 direction

The built-in Slurm and Kubernetes integrations remain preview lifecycle
adapters. A remote provider becomes supported only after its source,
environment, request/result, artifact, isolation, cancellation, recovery, and
unchanged-workload paths pass the ordinary release suite on named infrastructure.
Federation, additional stores, modalities, frameworks, and measured scale follow
the same rule.

Sampler replay remains explicitly unclaimed until sampler state is observed and
bound into atomic checkpoints. The local executor realizes hash-pinned binary
dependency locks into cached virtual environments layered over the module's
interpreter; it does not build source distributions or claim a closed runtime.
Offline candidate installation is tested, but full air-gap and
production-scale claims require direct deployment-specific evidence.

Change this roadmap when product evidence changes. Prefer deleting completed or
duplicated prose over growing another planning document.
