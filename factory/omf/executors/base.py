"""Executor adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ExecutionState = Literal["pending", "running", "succeeded", "failed", "canceled", "unknown"]


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
