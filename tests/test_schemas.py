from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from omf.errors import ValidationError
from omf.models import Metadata, finalize_resource
from omf.schema_registry import SchemaRegistry


def _value(schema, root):
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].split("/")[-1]]
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "oneOf" in schema and schema.get("type") != "object" and "properties" not in schema:
        return _value(schema["oneOf"][0], root)
    typ = schema.get("type")
    if typ == "object" or "properties" in schema:
        value = {
            key: _value(schema.get("properties", {})[key], root)
            for key in schema.get("required", [])
        }
        if "oneOf" in schema:
            choice = schema["oneOf"][0]
            for key in choice.get("required", []):
                value[key] = _value(schema["properties"][key], root)
        if not value and schema.get("minProperties", 0):
            value["component"] = _value(schema.get("additionalProperties", {}), root)
        return value
    if typ == "array":
        return [_value(schema.get("items", {}), root)] * schema.get("minItems", 0)
    if typ == "integer":
        return max(1, schema.get("minimum", 0))
    if typ == "number":
        return 1.0
    if typ == "boolean":
        return True
    if schema.get("format") == "uuid":
        return "00000000-0000-4000-8000-000000000000"
    pattern = schema.get("pattern", "")
    if pattern.startswith("^sha256:") or pattern.endswith(":[0-9a-f]+$"):
        return "sha256:" + "0" * 64
    return "x"


def _minimal(registry, kind):
    schema = registry.schema_for(kind)
    return _value(schema, schema)


@pytest.mark.parametrize("kind", SchemaRegistry().kinds)
def test_every_schema_kind_accepts_minimal_resource(kind):
    registry = SchemaRegistry()
    value = _minimal(registry, kind)
    assert registry.validate(value)["kind"] == kind


def test_schema_rejects_wrong_kind_top_level_and_naive_time():
    registry = SchemaRegistry()
    value = _minimal(registry, registry.kinds[0])
    value["kind"] = "NoSuchKind"
    with pytest.raises(ValidationError):
        registry.validate(value)
    Metadata(name="x", namespace="x", createdAt=datetime.now(UTC))
    naive = datetime(2026, 1, 1)  # noqa: DTZ001 - deliberately test rejection
    with pytest.raises(ValueError, match="timezone"):
        Metadata(name="x", namespace="x", createdAt=naive)


def test_finalization_excludes_status_and_does_not_mutate():
    source = {
        "apiVersion": "omf.dev/v1alpha1",
        "kind": "X",
        "metadata": {"name": "x", "namespace": "n"},
        "spec": {"a": 1},
        "status": {"phase": "old"},
    }
    original = deepcopy(source)
    first = finalize_resource(source, actor="a", now=datetime(2026, 1, 1, tzinfo=UTC))
    source2 = deepcopy(source)
    source2["status"] = {"phase": "new"}
    second = finalize_resource(source2, actor="a", now=datetime(2026, 1, 1, tzinfo=UTC))
    assert source == original
    assert first["metadata"]["revision"] == second["metadata"]["revision"]


def test_checked_in_deployment_manifest_is_valid():
    value = SchemaRegistry().load(Path("deployments/example-edge.yaml"))
    assert value["kind"] == "DeploymentSpec"
    assert value["metadata"]["namespace"] == "local/open-model-factory"


def test_checked_in_workload_and_modules_use_canonical_resources():
    registry = SchemaRegistry()
    workload = registry.load(Path("workloads/example-statistical.yaml"))
    statistical = registry.load(Path("modules/examples/statistical/module.yaml"))
    text_frequency = registry.load(Path("modules/examples/text-frequency/module.yaml"))
    from_scratch = registry.load(Path("workloads/example-from-scratch.yaml"))
    affine = registry.load(Path("modules/examples/affine-regression/module.yaml"))
    model_package = registry.load(Path("model-packages/example-affine.yaml"))
    assert workload["kind"] == "WorkloadSpec"
    assert from_scratch["kind"] == "WorkloadSpec"
    assert statistical["kind"] == text_frequency["kind"] == affine["kind"] == "Module"
    assert model_package["kind"] == "ModelPackage"

    with pytest.raises(ValidationError, match="unsupported apiVersion"):
        registry.validate({"stages": []})


def test_module_and_workload_references_are_typed_and_relocatable():
    registry = SchemaRegistry()
    module = registry.load(Path("modules/examples/statistical/module.yaml"))
    workload = registry.load(Path("workloads/example-statistical.yaml"))

    invalid_module = deepcopy(module)
    invalid_module["spec"]["entryPoint"]["codeRoot"] = "/tmp/module"
    with pytest.raises(ValidationError):
        registry.validate(invalid_module)

    invalid_environment = deepcopy(module)
    invalid_environment["spec"]["environment"] = {}
    with pytest.raises(ValidationError):
        registry.validate(invalid_environment)

    invalid_workload = deepcopy(workload)
    invalid_workload["spec"]["graph"]["stages"][0]["module"] = "../module.yaml"
    with pytest.raises(ValidationError):
        registry.validate(invalid_workload)

    with pytest.raises(ValidationError, match="expected Module"):
        registry.validate_as(workload, "Module")
