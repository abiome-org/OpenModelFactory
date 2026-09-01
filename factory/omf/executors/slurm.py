from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from omf.executors.base import ExecutionPlan, ExecutionState, ExecutionStatus, Executor


class SlurmExecutor(Executor):
    def __init__(self) -> None:
        self._dirs: dict[str, Path] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({"gang", "preemption", "signals", "checkpoint-hooks"})

    def preflight(self) -> list[str]:
        return [f"missing tool: {x}" for x in ("sbatch", "sacct", "scancel") if not shutil.which(x)]

    def plan(
        self,
        *,
        argv: list[str],
        run_dir: Path,
        cwd: Path,
        resources: dict[str, Any] | None = None,
        **_: Any,
    ) -> ExecutionPlan:
        r = resources or {}
        lines = ["#!/bin/sh", "set -eu"]
        for key, flag in (
            ("nodes", "nodes"),
            ("tasks", "ntasks"),
            ("cpus", "cpus-per-task"),
            ("gpus", "gpus"),
        ):
            if key in r:
                lines.append(f"#SBATCH --{flag}={int(r[key])}")
        lines.append("exec " + " ".join(_quote(x) for x in argv))
        return ExecutionPlan(
            ("sbatch", "--parsable", str(run_dir / "job.sh")),
            run_dir,
            cwd,
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


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
