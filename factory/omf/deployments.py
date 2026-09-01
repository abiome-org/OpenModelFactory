"""Revision-explicit deployment desired/observed state and rollback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from omf.errors import ConflictError, IntegrityError

Form = Literal["batch", "service", "actor", "edge", "control"]


@dataclass(frozen=True)
class DeploymentSpec:
    release_revision: str
    runtime_revision: str
    form: Form
    replicas: tuple[int, int] = (1, 1)
    rollout: str = "rolling"
    health_gates: tuple[str, ...] = ()
    routing: dict[str, Any] = field(default_factory=dict)
    sessions: dict[str, Any] = field(default_factory=dict)
    objectives: dict[str, float] = field(default_factory=dict)
    security: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeploymentStatus:
    desired_revision: str
    observed_revision: str | None
    version: int
    state: str
    previous_revision: str | None = None


class DeploymentAdapter(Protocol):
    def apply(self, spec: DeploymentSpec, revision: str) -> str: ...
    def rollback(self, revision: str) -> str: ...


class DeploymentService:
    def __init__(self, adapter: DeploymentAdapter, *, allow_test_adapter: bool = False) -> None:
        if getattr(adapter, "in_process_test_adapter", False) and not allow_test_adapter:
            raise IntegrityError("in-process deployment adapter is test-only")
        self.adapter = adapter
        self._statuses: dict[str, DeploymentStatus] = {}

    def deploy(
        self, name: str, spec: DeploymentSpec, revision: str, *, expected_version: int | None = None
    ) -> DeploymentStatus:
        old = self._statuses.get(name)
        current = None if old is None else old.version
        if current != expected_version:
            raise ConflictError("deployment status version mismatch")
        # Release identity is immutable and can only change via this explicit revision operation.
        observed = self.adapter.apply(spec, revision)
        status = DeploymentStatus(
            revision,
            observed,
            (current or 0) + 1,
            "healthy" if observed == revision else "degraded",
            old.desired_revision if old else None,
        )
        self._statuses[name] = status
        return status

    def status(self, name: str) -> DeploymentStatus:
        return self._statuses[name]

    def rollback(self, name: str, *, expected_version: int) -> DeploymentStatus:
        old = self._statuses[name]
        if old.version != expected_version or old.previous_revision is None:
            raise ConflictError("deployment cannot rollback at requested version")
        observed = self.adapter.rollback(old.previous_revision)
        status = DeploymentStatus(
            old.previous_revision, observed, old.version + 1, "rolled_back", old.desired_revision
        )
        self._statuses[name] = status
        return status


class LocalProcessAdapter:
    """Minimal local adapter around a supplied process controller."""

    def __init__(self, controller: Any) -> None:
        self.controller = controller

    def apply(self, spec: DeploymentSpec, revision: str) -> str:
        self.controller.start(spec, revision)
        return revision

    def rollback(self, revision: str) -> str:
        self.controller.activate(revision)
        return revision


class ExecutorDeploymentAdapter(LocalProcessAdapter):
    pass
