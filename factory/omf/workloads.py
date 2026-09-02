"""Validated workload DAG and atomic state history."""

from __future__ import annotations

import fcntl
import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from omf.canonical import portable_relative_path, sha256_digest
from omf.errors import ValidationError
from omf.schema_registry import default_registry


class RunState(StrEnum):
    DRAFT = "Draft"
    VALIDATED = "Validated"
    ADMITTED = "Admitted"
    RUNNING = "Running"
    RECOVERING = "Recovering"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    CANCELED = "Canceled"


_TRANSITIONS = {
    RunState.DRAFT: {RunState.VALIDATED, RunState.CANCELED},
    RunState.VALIDATED: {RunState.ADMITTED, RunState.FAILED, RunState.CANCELED},
    RunState.ADMITTED: {RunState.RUNNING, RunState.CANCELED},
    RunState.RUNNING: {RunState.RECOVERING, RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELED},
    RunState.RECOVERING: {RunState.RUNNING, RunState.FAILED, RunState.CANCELED},
}


class Stage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    needs: list[str] = Field(default_factory=list)
    module: str
    operation: str = "run"
    config: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)
    checkpoint_trigger: dict[str, Any] | None = Field(
        default=None, validation_alias="checkpointTrigger"
    )
    evaluation_trigger: dict[str, Any] | None = Field(
        default=None, validation_alias="evaluationTrigger"
    )
    idempotent: bool = False
    retries: int = 0


class AdmittedWorkload(BaseModel):
    """Internal execution projection of one canonical WorkloadSpec resource."""

    model_config = ConfigDict(extra="forbid")
    stages: list[Stage]
    source_digest: str
    binding_digest: str | None = None
    module_digests: dict[str, str] = Field(default_factory=dict)
    environments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    input_revisions: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    reproducibility: str = "lineage"
    model_package_ref: str | None = None
    mix_ref: str | None = None
    evaluation_refs: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    policies: list[str] = Field(default_factory=list)
    child_work: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def graph(self) -> AdmittedWorkload:
        names = [x.name for x in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique")
        known = set(names)
        for stage in self.stages:
            if stage.name in stage.needs or not set(stage.needs) <= known:
                raise ValueError(f"invalid dependency for {stage.name}")
            if stage.retries and not stage.idempotent:
                raise ValueError("retries require idempotent stage")
            for reference in stage.inputs.values():
                producer, separator, output = reference.partition(".")
                if separator and producer in known:
                    producer_stage = next(item for item in self.stages if item.name == producer)
                    if producer not in stage.needs:
                        raise ValueError(
                            f"stage {stage.name} input references {producer} without a dependency"
                        )
                    if output not in producer_stage.outputs:
                        raise ValueError(f"stage input references undeclared output: {reference}")
        self.topological_order()
        return self

    def topological_order(self) -> list[str]:
        pending = {s.name: set(s.needs) for s in self.stages}
        result = []
        while pending:
            ready = sorted(k for k, v in pending.items() if not v)
            if not ready:
                raise ValueError("workload graph contains a cycle")
            for name in ready:
                result.append(name)
                pending.pop(name)
            for needs in pending.values():
                needs.difference_update(ready)
        return result

    @property
    def digest(self) -> str:
        value = self.model_dump(mode="json", exclude={"binding_digest", "environments"})
        value["environmentDigests"] = {
            stage: descriptor["digest"] for stage, descriptor in sorted(self.environments.items())
        }
        return sha256_digest(value)


def project_workload(resource: Any) -> AdmittedWorkload:
    """Validate and project the only supported WorkloadSpec authoring contract."""
    value = default_registry.validate_as(resource, "WorkloadSpec")
    spec = value["spec"]
    try:
        stages = [Stage.model_validate(stage) for stage in spec["graph"]["stages"]]
        for stage in stages:
            portable_relative_path(stage.module, f"stage {stage.name!r} module")
        admitted = AdmittedWorkload(
            stages=stages,
            source_digest="pending",
            parameters=spec["parameters"],
            reproducibility=str(spec.get("reproducibility", "lineage")),
            model_package_ref=spec.get("modelPackageRef"),
            mix_ref=spec.get("mixRef"),
            evaluation_refs=spec.get("evaluationRefs", []),
            budget=spec.get("budget", {}),
            policies=spec.get("policies", []),
            child_work=spec.get("childWork", {}),
        )
    except PydanticValidationError as exc:
        raise ValidationError(
            "workload semantic validation failed",
            details={"errors": exc.error_count()},
        ) from exc
    unsupported = [
        field
        for field in (
            "parameters",
            "budget",
            "policies",
            "childWork",
        )
        if spec.get(field)
    ]
    if any(stage.checkpoint_trigger or stage.evaluation_trigger for stage in stages):
        unsupported.append("stageTriggers")
    if any(stage.retries for stage in stages):
        unsupported.append("retries")
    if unsupported:
        raise ValidationError(
            "workload requests lifecycle features that are not executable",
            details={"fields": unsupported},
        )
    reproducibility = admitted.reproducibility
    if reproducibility != "lineage":
        raise ValidationError(
            f"reproducibility class {reproducibility!r} is not executable; use 'lineage'"
        )
    desired = {
        "apiVersion": value["apiVersion"],
        "kind": value["kind"],
        "metadata": {
            "name": value["metadata"]["name"],
            "namespace": value["metadata"]["namespace"],
        },
        "spec": spec,
    }
    return admitted.model_copy(update={"source_digest": sha256_digest(desired)})


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self, spec: AdmittedWorkload) -> None:
        if not self.path.exists():
            self._write(
                {
                    "version": 0,
                    "state": "Draft",
                    "history": [],
                    "stages": {},
                    "digests": {
                        "workload": spec.digest,
                        "workloadManifest": spec.source_digest,
                        "binding": spec.binding_digest,
                        "modules": spec.module_digests,
                        "environments": spec.environments,
                        "inputs": spec.input_revisions,
                        "modelPackage": spec.model_package_ref,
                        "reproducibility": spec.reproducibility,
                    },
                }
            )

    def _write(self, value: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def read(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.path.read_text())
        return value

    def transition(
        self, expected: RunState, target: RunState, reason: str | None = None
    ) -> dict[str, Any]:
        lock = self.path.with_suffix(".lock")
        with lock.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            value = self.read()
            if value["state"] != expected.value:
                raise RuntimeError("state compare-and-set conflict")
            if target not in _TRANSITIONS.get(expected, set()):
                raise ValueError("invalid state transition")
            value["version"] += 1
            value["state"] = target.value
            value["history"].append(
                {
                    "from": expected.value,
                    "to": target.value,
                    "reason": reason,
                    "version": value["version"],
                }
            )
            self._write(value)
            return value


class WorkloadRunner:
    """Mechanical synchronous runner; callable receives Stage and returns output digest mapping."""

    def __init__(self, spec: AdmittedWorkload, store: StateStore) -> None:
        self.spec = spec
        self.store = store
        store.initialize(spec)

    def run(self, execute: Any, verify: Any = lambda outputs: True) -> dict[str, Any]:
        value = self.store.read()
        for name in self.spec.topological_order():
            stage = next(x for x in self.spec.stages if x.name == name)
            old = value["stages"].get(name)
            if old and old.get("status") == "succeeded" and verify(old.get("outputs", {})):
                continue
            for attempt in range(stage.retries + 1):
                try:
                    outputs = execute(stage)
                    value["stages"][name] = {
                        "status": "succeeded",
                        "attempt": attempt + 1,
                        "outputs": outputs,
                    }
                    self.store._write(value)
                    break
                except Exception:
                    if attempt == stage.retries:
                        value["stages"][name] = {"status": "failed", "attempt": attempt + 1}
                        self.store._write(value)
                        raise
        return value
