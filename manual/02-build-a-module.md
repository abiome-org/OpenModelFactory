# 2. Build a module

**Status: Tested now**

## Outcome

You will package one model, trainer, objective, evaluator, transform, generator,
or environment role behind the same `omf.module/v1` request/result boundary.
OMF does not require a framework, modality, tensor shape, tokenizer, or model
architecture.

## Begin with the protocol

Study the checked-in [statistical module manifest](../modules/examples/statistical/module.yaml)
and [implementation](../modules/examples/statistical/main.py). A module owns:

- its code root and deterministic argument vector;
- typed input and output schema identifiers;
- platform and capability requirements;
- resource and timeout limits;
- declared secrets, network access, and side effects;
- checkpoint and determinism declarations;
- contract fixtures that can execute without a training run.

Create a directory under the appropriate role in `modules/`, place a
`module.yaml` at its root, and implement operations through the SDK protocol.
Paths and artifacts returned by a module must remain inside its stage run
directory. Declare every network or secret requirement rather than reaching
around the protocol.

Validate the source boundary and execute its fixtures:

```sh
omf --output json module validate modules/<role>/<name>/module.yaml
omf --output json module test modules/<role>/<name>/module.yaml
```

Validation captures exact source into a content-addressed package. Testing
checks protocol fixtures; it does not establish scientific quality. Add fixtures
for malformed input, deterministic replay, resource limits, and declared error
results as the module matures.

## Module versus model package

Workload stages and serving adapters both use `module.yaml`, but they are
separate implementations. A versioned `ModelPackage` binds the training stage's
state output to an independent inference module, compatibility vectors,
provenance, and serving requirements. OMF captures both sources at run
admission; compatibility evaluation never reloads mutable checkout code.
Passing `module test` alone does not establish model compatibility.

## Evidence before the next chapter

- `module validate` reports `valid: true` and immutable package/artifact digests.
- `module test` passes every declared fixture.
- Capabilities, resources, side effects, network, and secrets are explicit.
- Model-specific assumptions remain inside the module, not the factory core.
