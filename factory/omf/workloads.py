"""Validated workload DAG and atomic state history."""

from __future__ import annotations

import fcntl
import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omf.canonical import sha256_digest


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
    model_config = ConfigDict(extra="forbid")
    name: str
    needs: list[str] = Field(default_factory=list)
    module: str
    operation: str = "run"
    config: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)
    checkpoint_trigger: dict[str, Any] | None = None
    evaluation_trigger: dict[str, Any] | None = None
    idempotent: bool = False
    retries: int = 0


class WorkloadSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stages: list[Stage]
    workload_digest: str | None = None
    binding_digest: str | None = None
    module_digests: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def graph(self) -> WorkloadSpec:
        names = [x.name for x in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique")
        known = set(names)
        for stage in self.stages:
            if stage.name in stage.needs or not set(stage.needs) <= known:
                raise ValueError(f"invalid dependency for {stage.name}")
            if stage.retries and not stage.idempotent:
                raise ValueError("retries require idempotent stage")
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
        return sha256_digest(
            self.model_dump(mode="json", exclude={"workload_digest", "binding_digest"})
        )


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self, spec: WorkloadSpec) -> None:
        if not self.path.exists():
            self._write(
                {
                    "version": 0,
                    "state": "Draft",
                    "history": [],
                    "stages": {},
                    "digests": {
                        "workload": spec.digest,
                        "binding": spec.binding_digest,
                        "modules": spec.module_digests,
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

    def __init__(self, spec: WorkloadSpec, store: StateStore) -> None:
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
