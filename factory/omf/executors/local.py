"""Local no-shell executor with durable protocol files and honest isolation."""

from __future__ import annotations

import hashlib
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

from omf.canonical import sha256_digest
from omf.errors import CapabilityError
from omf.executors.base import (
    DEPLOYMENT_PROTOCOL_CAPABILITIES,
    MODULE_PROTOCOL_CAPABILITIES,
    DependencyLock,
    ExecutionPlan,
    ExecutionStatus,
    Executor,
)


class LocalExecutor(Executor):
    def __init__(
        self,
        *,
        binding_resources: dict[str, Any] | None = None,
        binding_spec: dict[str, Any] | None = None,
    ) -> None:
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._dirs: dict[str, Path] = {}
        self.binding_resources = binding_resources or {}
        self.binding_spec = binding_spec or {}

    @property
    def capabilities(self) -> frozenset[str]:
        caps = {
            "environment:executable-drift-detection",
            "process-group",
            "rlimit",
            *MODULE_PROTOCOL_CAPABILITIES,
            *DEPLOYMENT_PROTOCOL_CAPABILITIES,
        }
        if self._network_namespace_available():
            caps.update({"network-namespace", "isolation:network-deny"})
        return frozenset(caps)

    @staticmethod
    def _find_executable(name: str) -> Path | None:
        value = shutil.which(name)
        if value is None:
            return None
        try:
            return Path(value).resolve(strict=True)
        except OSError:
            return None

    @classmethod
    def _network_namespace_available(cls, unshare: Path | None = None) -> bool:
        unshare = unshare or cls._find_executable("unshare")
        if unshare is None or os.name != "posix":
            return False
        result = subprocess.run(
            [str(unshare), "--user", "--map-root-user", "--net", "true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def preflight(self) -> list[str]:
        issues = [] if os.name == "posix" else ["POSIX resource limits unavailable"]
        supported = {"cpu", "memory", "accelerators"}
        if unknown := sorted(set(self.binding_resources) - supported):
            issues.append(f"unsupported local binding resources: {', '.join(unknown)}")
        if self.binding_resources.get("cpu", "auto") != "auto":
            issues.append("local executor cannot enforce an explicit CPU count")
        if self.binding_resources.get("memory", "auto") != "auto":
            issues.append("local executor cannot enforce an explicit memory quantity")
        if self.binding_resources.get("accelerators", []):
            issues.append("local executor cannot qualify accelerator placement")
        for field in ("placement", "transport", "extensions"):
            if self.binding_spec.get(field):
                issues.append(f"local executor does not support Binding.spec.{field}")
        config = self.binding_spec.get("config", {})
        if not isinstance(config, dict):
            issues.append("local Binding.spec.config must be an object")
            return issues
        supported_config = {"executor", "stores", "isolation", "recovery"}
        if unknown := sorted(set(config) - supported_config):
            issues.append(f"unsupported local Binding config: {', '.join(unknown)}")
        stores = config.get("stores", {})
        if stores and stores != {"artifacts": "local", "checkpoints": "local"}:
            issues.append("local executor supports only local artifact and checkpoint stores")
        isolation = config.get("isolation", {})
        if isolation and isolation != {"driver": "subprocess", "network": "deny"}:
            issues.append("local executor supports only subprocess isolation with network denial")
        recovery = config.get("recovery", {})
        if recovery and recovery != {"attempts": 1, "checkpointOnCancel": False}:
            issues.append("local executor supports one attempt without checkpoint-on-cancel")
        return issues

    def prepare_environment(
        self,
        *,
        argv: list[str],
        cwd: Path,
        dependency: DependencyLock,
        deny_network: bool = False,
    ) -> dict[str, Any]:
        if dependency.contents:
            raise CapabilityError(
                "local executor cannot realize a non-empty dependency lock",
                details={"dependencyDigest": dependency.digest},
            )
        executable = argv[0]
        resolved = (
            (cwd / executable).resolve() if "/" in executable else self._find_executable(executable)
        )
        if resolved is None or not resolved.is_file():
            raise RuntimeError(f"module executable is unavailable: {executable}")
        executable_bytes = resolved.read_bytes()
        if executable_bytes.startswith(b"#!"):
            raise RuntimeError("script entry points must declare their interpreter explicitly")
        executables = [
            {
                "role": "module",
                "path": str(resolved),
                "digest": "sha256:" + hashlib.sha256(executable_bytes).hexdigest(),
            }
        ]
        wrapper: list[str] = []
        if deny_network:
            unshare = self._find_executable("unshare")
            if unshare is None or not self._network_namespace_available(unshare):
                raise RuntimeError("network denial unavailable")
            wrapper = [str(unshare), "--user", "--map-root-user", "--net", "--"]
            executables.insert(
                0,
                {
                    "role": "network-isolation",
                    "path": str(unshare),
                    "digest": "sha256:" + hashlib.sha256(unshare.read_bytes()).hexdigest(),
                },
            )
        descriptor: dict[str, Any] = {
            "requestedCommand": list(argv),
            "command": [str(resolved), *argv[1:]],
            "wrapper": wrapper,
            "dependencyDigest": dependency.digest,
            "executables": executables,
        }
        descriptor["digest"] = sha256_digest(
            {
                "requestedCommand": descriptor["requestedCommand"],
                "dependencyDigest": dependency.digest,
                "executables": [
                    {"role": item["role"], "digest": item["digest"]} for item in executables
                ],
            }
        )
        return descriptor

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
        environment: dict[str, Any] | None = None,
        **_: Any,
    ) -> ExecutionPlan:
        command = tuple(argv)
        if deny_network:
            wrapper = tuple((environment or {}).get("wrapper", ()))
            if not wrapper:
                raise RuntimeError("network denial unavailable")
            command = (*wrapper, *command)
        return ExecutionPlan(
            command,
            run_dir,
            cwd,
            env or {},
            {**self.binding_resources, **(resources or {})},
            timeout,
            deny_network,
            {
                "requiresResult": requires_result,
                "environmentDigest": (environment or {}).get("digest"),
                "executables": (environment or {}).get("executables", []),
                "argvDigest": sha256_digest(list(command)),
            },
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
            *(
                [
                    item
                    for executable in plan.metadata.get("executables", [])
                    for item in (
                        "--attested-executable",
                        str(executable["path"]),
                        str(executable["digest"]),
                    )
                ]
            ),
            *(
                ["--environment-digest", str(plan.metadata["environmentDigest"])]
                if plan.metadata.get("environmentDigest")
                else []
            ),
            "--argv-digest",
            str(plan.metadata["argvDigest"]),
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
                    "environmentDigest": plan.metadata.get("environmentDigest"),
                    "argvDigest": plan.metadata["argvDigest"],
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

    def attach(self, execution_id: str, run_dir: Path) -> None:
        record = json.loads((run_dir / "execution.json").read_text())
        if str(record["id"]) != execution_id:
            raise RuntimeError("execution identity does not match the run directory")
        self._dirs[execution_id] = run_dir

    def reconcile(self, run_dir: Path) -> str:
        record = json.loads((run_dir / "execution.json").read_text())
        execution_id = str(record["id"])
        self.attach(execution_id, run_dir)
        return execution_id
