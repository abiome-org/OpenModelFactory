"""Executor adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ExecutionState = Literal["pending", "running", "succeeded", "failed", "canceled", "unknown"]

# These capabilities mean that an executor can carry the complete module protocol across its
# execution boundary. Scheduler submission alone is deliberately not enough.
MODULE_PROTOCOL_CAPABILITIES = frozenset(
    {
        "protocol:omf.module/v1",
        "transport:module-source",
        "transport:request-result",
        "transport:artifacts",
    }
)
DEPLOYMENT_PROTOCOL_CAPABILITIES = frozenset({"protocol:omf.deployment/v1"})


@dataclass(frozen=True)
class ExecutionPlan:
    argv: tuple[str, ...]
    run_dir: Path
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    resources: dict[str, int | float] = field(default_factory=dict)
    timeout: float | None = None
    deny_network: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionStatus:
    state: ExecutionState
    reason: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True)
class DependencyLock:
    """Exact opaque dependency declaration supplied to an executor provider."""

    relative_path: str
    digest: str
    contents: bytes = field(repr=False)


class Executor(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> frozenset[str]: ...
    @abstractmethod
    def preflight(self) -> list[str]: ...
    @abstractmethod
    def plan(
        self, *, argv: list[str], run_dir: Path, cwd: Path, **kwargs: Any
    ) -> ExecutionPlan: ...
    @abstractmethod
    def submit(self, plan: ExecutionPlan) -> str: ...
    @abstractmethod
    def status(self, execution_id: str) -> ExecutionStatus: ...
    @abstractmethod
    def cancel(self, execution_id: str) -> None: ...
    @abstractmethod
    def logs(self, execution_id: str) -> tuple[Path, Path]: ...

    def attach(self, execution_id: str, run_dir: Path) -> None:
        """Restore controller-local bookkeeping after a process restart.

        Executors whose scheduler identity is sufficient may keep the default no-op. Adapters
        that use the local run directory for status or logs should override this method.
        """
        del execution_id, run_dir

    def prepare_environment(
        self,
        *,
        argv: list[str],
        cwd: Path,
        dependency: DependencyLock,
        deny_network: bool = False,
    ) -> dict[str, Any]:
        """Pin an executable environment or fail before run allocation."""
        del argv, cwd, dependency, deny_network
        raise RuntimeError("executor cannot attest the declared module environment")
