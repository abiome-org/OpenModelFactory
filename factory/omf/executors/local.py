"""Local no-shell executor with durable protocol files and honest isolation."""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from omf.executors.base import ExecutionPlan, ExecutionStatus, Executor


class LocalExecutor(Executor):
    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._dirs: dict[str, Path] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        caps = {"process-group", "rlimit"}
        if self._network_namespace_available():
            caps.add("network-namespace")
        return frozenset(caps)

    @staticmethod
    def _network_namespace_available() -> bool:
        if not shutil.which("unshare") or os.name != "posix":
            return False
        result = subprocess.run(
            ["unshare", "--user", "--map-root-user", "--net", "true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def preflight(self) -> list[str]:
        return [] if os.name == "posix" else ["POSIX resource limits unavailable"]

    def plan(
        self,
        *,
        argv: list[str],
        run_dir: Path,
        cwd: Path,
        env: dict[str, str] | None = None,
        resources: dict[str, int | float] | None = None,
        timeout: float | None = None,
        deny_network: bool = False,
        requires_result: bool = True,
        **_: Any,
    ) -> ExecutionPlan:
        command = tuple(argv)
        if deny_network:
            if "network-namespace" not in self.capabilities:
                raise RuntimeError("network denial unavailable")
            command = (
                "unshare",
                "--user",
                "--map-root-user",
                "--net",
                "--",
                *command,
            )
        return ExecutionPlan(
            command,
            run_dir,
            cwd,
            env or {},
            resources or {},
            timeout,
            deny_network,
            {"requiresResult": requires_result},
        )

    def submit(self, plan: ExecutionPlan) -> str:
        execution_id = str(uuid.uuid4())
        plan.run_dir.mkdir(parents=True, exist_ok=True)
        request, result = plan.run_dir / "request.json", plan.run_dir / "result.json"
        if not request.exists():
            request.write_text("{}")
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "TZ"}}
        env.update(plan.env)
        env.update(
            {
                "OMF_REQUEST_FILE": str(request),
                "OMF_RESULT_FILE": str(result),
                "OMF_RUN_ID": execution_id,
            }
        )
        limits = plan.resources
        completion = plan.run_dir / "completion.json"
        worker = [
            sys.executable,
            "-m",
            "omf.executors.local_worker",
            "--completion",
            str(completion),
            *(["--timeout", str(plan.timeout)] if plan.timeout else []),
            "--",
            *plan.argv,
        ]

        def setup() -> None:
            os.setsid()
            mapping = {
                "cpu_seconds": resource.RLIMIT_CPU,
                "address_space": resource.RLIMIT_AS,
                "processes": resource.RLIMIT_NPROC,
                "file_size": resource.RLIMIT_FSIZE,
            }
            for key, kind in mapping.items():
                if key in limits:
                    resource.setrlimit(kind, (int(limits[key]), int(limits[key])))

        stdout, stderr = (
            (plan.run_dir / "stdout.log").open("ab"),
            (plan.run_dir / "stderr.log").open("ab"),
        )
        try:
            process = subprocess.Popen(
                worker, cwd=plan.cwd, env=env, stdout=stdout, stderr=stderr, preexec_fn=setup
            )
        finally:
            stdout.close()
            stderr.close()
        identity = self._identity(process.pid)
        (plan.run_dir / "execution.json").write_text(
            json.dumps(
                {
                    "id": execution_id,
                    "pid": process.pid,
                    "identity": identity,
                    "started": time.time(),
                    "timeout": plan.timeout,
                    "requiresResult": bool(plan.metadata.get("requiresResult", True)),
                }
            )
        )
        self._processes[execution_id] = process
        self._dirs[execution_id] = plan.run_dir
        return execution_id

    @staticmethod
    def _identity(pid: int) -> str | None:
        try:
            return Path(f"/proc/{pid}/stat").read_text().split()[21]
        except (OSError, IndexError):
            return None

    def _record(self, execution_id: str) -> tuple[Path, dict[str, Any]]:
        directory = self._dirs.get(execution_id)
        if directory is None:
            raise KeyError(execution_id)
        return directory, json.loads((directory / "execution.json").read_text())

    def status(self, execution_id: str) -> ExecutionStatus:
        directory, record = self._record(execution_id)
        process = self._processes.get(execution_id)
        completion = directory / "completion.json"
        if completion.exists():
            value = json.loads(completion.read_text())
            code = int(value["exitCode"])
            reason = str(value["reason"])
            if reason.startswith("signal:"):
                return ExecutionStatus("canceled", reason, code)
            if code == 0 and (
                not record.get("requiresResult", True) or (directory / "result.json").exists()
            ):
                return ExecutionStatus("succeeded", exit_code=code)
            return ExecutionStatus("failed", reason, code)
        process_code = process.poll() if process else None
        alive = self._identity(record["pid"]) == record["identity"]
        if process is None and alive:
            return ExecutionStatus("running")
        if process_code is None and alive:
            timeout = record.get("timeout")
            if timeout and time.time() - record["started"] > timeout:
                self.cancel(execution_id)
                return ExecutionStatus("failed", "timeout")
            return ExecutionStatus("running")
        return ExecutionStatus(
            "failed",
            "signal" if process_code is not None and process_code < 0 else "nonzero-exit",
            process_code,
        )

    def cancel(self, execution_id: str) -> None:
        directory, record = self._record(execution_id)
        if self._identity(record["pid"]) != record["identity"]:
            return
        completion = directory / "completion.json"
        os.killpg(record["pid"], signal.SIGTERM)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if completion.exists():
                return
            if self._identity(record["pid"]) != record["identity"]:
                break
            time.sleep(0.05)
        if self._identity(record["pid"]) == record["identity"]:
            os.killpg(record["pid"], signal.SIGKILL)
        if not completion.exists():
            temporary = completion.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "exitCode": -signal.SIGTERM,
                        "reason": "signal:SIGTERM",
                        "finished": time.time(),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            os.replace(temporary, completion)

    def logs(self, execution_id: str) -> tuple[Path, Path]:
        directory, _ = self._record(execution_id)
        return directory / "stdout.log", directory / "stderr.log"

    def reconcile(self, run_dir: Path) -> str:
        record = json.loads((run_dir / "execution.json").read_text())
        execution_id = str(record["id"])
        self._dirs[execution_id] = run_dir
        return execution_id
