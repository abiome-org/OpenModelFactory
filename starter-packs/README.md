# Optional starter packs

Starter packs are copyable examples, never factory-core dependencies. A pack may
choose a framework, model family, modality, or hardware stack, but must export the
same canonical `Module`, `ModelPackage`, `WorkloadSpec`, and artifact protocols.

The affine golden example in `modules/examples/affine-regression/`, its package
in `model-packages/`, evaluation in `evaluations/`, mix in `mixes/`, and workload
in `workloads/example-from-scratch.yaml` require no framework or model dependency
beyond the OMF runtime.
Framework-specific packs belong in separate subdirectories or distributions with
their own locked dependencies and capability declarations.
