from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from omf.executors.base import (
    MODULE_PROTOCOL_CAPABILITIES,
    ExecutionPlan,
    ExecutionState,
    ExecutionStatus,
    Executor,
)


class SlurmExecutor(Executor):
    def __init__(
        self,
        *,
        shared_filesystem: bool = False,
        binding_resources: dict[str, Any] | None = None,
        placement: dict[str, Any] | None = None,
        binding_spec: dict[str, Any] | None = None,
    ) -> None:
        self.shared_filesystem = shared_filesystem
        self.binding_resources = binding_resources or {}
        self.placement = placement or {}
        self.binding_spec = binding_spec or {}
        self._dirs: dict[str, Path] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        capabilities = {"gang", "preemption", "signals", "checkpoint-hooks"}
        if self.shared_filesystem:
            capabilities.update(MODULE_PROTOCOL_CAPABILITIES)
        return frozenset(capabilities)

    def preflight(self) -> list[str]:
        issues = [
            f"missing tool: {x}" for x in ("sbatch", "sacct", "scancel") if not shutil.which(x)
        ]
        supported_resources = {"nodes", "tasks", "cpus", "gpus"}
        if unknown := sorted(set(self.binding_resources) - supported_resources):
            issues.append(f"unsupported Slurm binding resources: {', '.join(unknown)}")
        for key in sorted(set(self.binding_resources) & supported_resources):
            value = self.binding_resources[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                issues.append(f"Slurm resource {key} must be a positive integer")
        supported_placement = {"partition", "account", "qos", "constraint", "reservation"}
        if unknown := sorted(set(self.placement) - supported_placement):
            issues.append(f"unsupported Slurm placement fields: {', '.join(unknown)}")
        for key in sorted(set(self.placement) & supported_placement):
            if not isinstance(self.placement[key], str) or not self.placement[key]:
                issues.append(f"Slurm placement {key} must be a non-empty string")
        for field in ("transport", "extensions"):
            if self.binding_spec.get(field):
                issues.append(f"built-in Slurm executor does not support Binding.spec.{field}")
        config = self.binding_spec.get("config", {})
        if isinstance(config, dict):
            if unsupported := sorted(
                key for key, value in config.items() if key != "executor" and value
            ):
                issues.append(
                    f"unsupported built-in Slurm Binding config: {', '.join(unsupported)}"
                )
        elif config:
            issues.append("Slurm Binding.spec.config must be an object")
        return issues

    def plan(
        self,
        *,
        argv: list[str],
        run_dir: Path,
        cwd: Path,
        resources: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        deny_network: bool = False,
        requires_result: bool = False,
        **_: Any,
    ) -> ExecutionPlan:
        if requires_result and not self.shared_filesystem:
            raise RuntimeError("module execution requires a declared shared filesystem")
        if deny_network:
            raise RuntimeError("the built-in Slurm adapter cannot enforce network denial")
        r = {**self.binding_resources, **(resources or {})}
        lines = [
            "#!/bin/sh",
            f"#SBATCH --output={_quote(str(run_dir / 'slurm-%j.out'))}",
            f"#SBATCH --error={_quote(str(run_dir / 'slurm-%j.err'))}",
        ]
        for key, flag in (
            ("nodes", "nodes"),
            ("tasks", "ntasks"),
            ("cpus", "cpus-per-task"),
            ("gpus", "gpus"),
        ):
            if key in r:
                lines.append(f"#SBATCH --{flag}={int(r[key])}")
        for key, flag in (
            ("partition", "partition"),
            ("account", "account"),
            ("qos", "qos"),
            ("constraint", "constraint"),
            ("reservation", "reservation"),
        ):
            if key in self.placement:
                lines.append(f"#SBATCH --{flag}={_quote(str(self.placement[key]))}")
        if timeout is not None:
            lines.append(f"#SBATCH --time={max(1, int((timeout + 59) // 60))}")
        lines.append("set -eu")
        environment = {
            **(env or {}),
            "OMF_REQUEST_FILE": str(run_dir / "request.json"),
            "OMF_RESULT_FILE": str(run_dir / "result.json"),
        }
        for key, value in sorted(environment.items()):
            lines.append(f"export {key}={_quote(value)}")
        lines.append('export OMF_RUN_ID="${SLURM_JOB_ID}"')
        lines.append("exec " + " ".join(_quote(x) for x in argv))
        return ExecutionPlan(
            ("sbatch", "--parsable", str(run_dir / "job.sh")),
            run_dir,
            cwd,
            env or {},
            r,
            timeout,
            deny_network,
            metadata={"script": "\n".join(lines) + "\n"},
        )

    def submit(self, plan: ExecutionPlan) -> str:
        plan.run_dir.mkdir(parents=True, exist_ok=True)
        (plan.run_dir / "job.sh").write_text(plan.metadata["script"])
        value = subprocess.run(
            plan.argv, cwd=plan.cwd, check=True, capture_output=True, text=True
        ).stdout.strip()
        match = re.fullmatch(r"(\d+)(?:;.*)?", value)
        if not match:
            raise RuntimeError("invalid sbatch job id")
        self._dirs[match[1]] = plan.run_dir
        return match[1]

    def status(self, execution_id: str) -> ExecutionStatus:
        state = (
            subprocess.run(
                ["sacct", "-j", execution_id, "-n", "-X", "-o", "State"],
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .split()[0]
        )
        states: dict[str, ExecutionState] = {
            "COMPLETED": "succeeded",
            "RUNNING": "running",
            "PENDING": "pending",
            "CANCELLED": "canceled",
        }
        return ExecutionStatus(states.get(state.split("+")[0], "failed"))

    def cancel(self, execution_id: str) -> None:
        subprocess.run(["scancel", "--signal=TERM", execution_id], check=True)

    def logs(self, execution_id: str) -> tuple[Path, Path]:
        d = self._dirs[execution_id]
        return d / f"slurm-{execution_id}.out", d / f"slurm-{execution_id}.err"

    def attach(self, execution_id: str, run_dir: Path) -> None:
        self._dirs[execution_id] = run_dir


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
