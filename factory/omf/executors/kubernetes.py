from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from omf.executors.base import ExecutionPlan, ExecutionState, ExecutionStatus, Executor


class KubernetesExecutor(Executor):
    def __init__(self, context: str | None = None) -> None:
        self.context = context
        self._dirs: dict[str, Path] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({"job", "server-dry-run", "jobset-plan"})

    def _base(self) -> list[str]:
        return ["kubectl", *(["--context", self.context] if self.context else [])]

    def preflight(self) -> list[str]:
        if not shutil.which("kubectl"):
            return ["missing tool: kubectl"]
        result = subprocess.run([*self._base(), "cluster-info"], capture_output=True)
        return [] if result.returncode == 0 else ["kubectl context unavailable"]

    def plan(
        self,
        *,
        argv: list[str],
        run_dir: Path,
        cwd: Path,
        image: str | None = None,
        name: str = "omf-job",
        roles: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> ExecutionPlan:
        if image is None or not re.search(r"@sha256:[0-9a-f]{64}$", image):
            raise ValueError("immutable image digest required")
        if roles:
            resource = {
                "apiVersion": "jobset.x-k8s.io/v1alpha2",
                "kind": "JobSet",
                "metadata": {"name": name},
                "spec": {"replicatedJobs": roles},
            }
        else:
            resource = {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {"name": name},
                "spec": {
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "containers": [{"name": "module", "image": image, "command": argv}],
                        }
                    }
                },
            }
        path = run_dir / "resource.json"
        return ExecutionPlan(
            (*self._base(), "apply", "-f", str(path)),
            run_dir,
            cwd,
            metadata={"resource": resource, "name": name},
        )

    def submit(self, plan: ExecutionPlan) -> str:
        plan.run_dir.mkdir(parents=True, exist_ok=True)
        path = plan.run_dir / "resource.json"
        path.write_text(
            json.dumps(plan.metadata["resource"], sort_keys=True, separators=(",", ":"))
        )
        subprocess.run(
            [*self._base(), "apply", "--server-side", "--dry-run=server", "-f", str(path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(plan.argv, check=True, capture_output=True)
        name = str(plan.metadata["name"])
        self._dirs[name] = plan.run_dir
        return name

    def status(self, execution_id: str) -> ExecutionStatus:
        value = json.loads(
            subprocess.run(
                [*self._base(), "get", "job", execution_id, "-o", "json"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        status = value.get("status", {})
        state: ExecutionState = (
            "succeeded"
            if status.get("succeeded")
            else "failed"
            if status.get("failed")
            else "running"
            if status.get("active")
            else "pending"
        )
        return ExecutionStatus(state)

    def cancel(self, execution_id: str) -> None:
        subprocess.run([*self._base(), "delete", "job", execution_id, "--wait=false"], check=True)

    def logs(self, execution_id: str) -> tuple[Path, Path]:
        d = self._dirs[execution_id]
        out = d / "stdout.log"
        err = d / "stderr.log"
        result = subprocess.run([*self._base(), "logs", f"job/{execution_id}"], capture_output=True)
        out.write_bytes(result.stdout)
        err.write_bytes(result.stderr)
        return out, err

    def attach(self, execution_id: str, run_dir: Path) -> None:
        self._dirs[execution_id] = run_dir
