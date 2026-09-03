import hashlib
import shutil
import sys
import time
from types import SimpleNamespace

import pytest
from omf.errors import CapabilityError, ConfigurationError, IntegrityError, ValidationError
from omf.executors import ExecutorContext, ExecutorProvider, ExecutorRegistry
from omf.executors.base import DependencyLock
from omf.executors.kubernetes import KubernetesExecutor
from omf.executors.local import LocalExecutor
from omf.executors.registry import default_executor_registry
from omf.executors.slurm import SlurmExecutor


def test_deterministic_plans_and_preflight(tmp_path, monkeypatch):
    slurm = SlurmExecutor()
    args = {
        "argv": ["echo", "a b"],
        "run_dir": tmp_path,
        "cwd": tmp_path,
        "resources": {"nodes": 2},
    }
    assert slurm.plan(**args).metadata == slurm.plan(**args).metadata
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert len(slurm.preflight()) == 3
    kube = KubernetesExecutor()
    with pytest.raises(ValueError, match="immutable"):
        kube.plan(argv=["x"], run_dir=tmp_path, cwd=tmp_path, image="latest")


def test_executor_registry_catalog_duplicates_unknown_and_discovery(tmp_path, monkeypatch):
    registry = default_executor_registry(discover=False)
    catalog = registry.catalog()
    assert [item["name"] for item in catalog["providers"]] == [
        "kubernetes",
        "local",
        "slurm",
    ]
    with pytest.raises(ConfigurationError, match="duplicate"):
        registry.register(
            ExecutorProvider("local", lambda _context: LocalExecutor()), source="test"
        )
    with pytest.raises(CapabilityError, match="unknown"):
        registry.resolve(
            "missing",
            project_root=tmp_path,
            state_root=tmp_path / ".omf",
            actor="tester",
            declaration={},
        )

    custom = ExecutorRegistry()
    provider = ExecutorProvider(
        "custom",
        lambda context: LocalExecutor() if isinstance(context, ExecutorContext) else None,
        config_contract={"type": "object", "additionalProperties": False},
    )
    entry_point = SimpleNamespace(
        name="custom",
        value="example.provider:provider",
        dist=SimpleNamespace(name="example-provider"),
        load=lambda: provider,
    )
    entry_points = SimpleNamespace(select=lambda **_kwargs: [entry_point])
    monkeypatch.setattr("omf.executors.registry.metadata.entry_points", lambda: entry_points)
    custom.discover()
    assert custom.catalog()["providers"][0]["source"].startswith("entry-point:example-provider")

    with pytest.raises(ValidationError, match="provider contract") as invalid:
        custom.resolve(
            "custom",
            project_root=tmp_path,
            state_root=tmp_path / ".omf",
            actor="tester",
            declaration={},
            config={"submitted-secret": "must-not-be-returned"},
        )
    assert "must-not-be-returned" not in repr(invalid.value.as_dict())

    injected = ExecutorRegistry()
    injected.register(provider)
    assert injected.catalog()["providers"][0]["source"] == "runtime"


def test_executor_registry_rejects_invalid_plugins_and_controller_fields(tmp_path, monkeypatch):
    registry = ExecutorRegistry()
    with pytest.raises(ConfigurationError, match="normalized"):
        registry.register(ExecutorProvider(" invalid ", lambda _context: LocalExecutor()))
    with pytest.raises(ConfigurationError, match="invalid config contract"):
        registry.register(
            ExecutorProvider(
                "invalid-schema",
                lambda _context: LocalExecutor(),
                config_contract={"type": "not-a-json-schema-type"},
            )
        )

    provider = ExecutorProvider("valid", lambda _context: LocalExecutor())
    registry.register(provider)
    resolve = {
        "project_root": tmp_path,
        "state_root": tmp_path / ".omf",
        "actor": "tester",
        "declaration": {},
    }
    with pytest.raises(ValidationError, match="controller-owned"):
        registry.resolve("valid", config={"argv": ["unexpected"]}, **resolve)

    builtins = default_executor_registry(discover=False)
    with pytest.raises(ValidationError, match="provider contract"):
        builtins.resolve("kubernetes", config={"typo": True}, **resolve)

    invalid_executor = ExecutorRegistry()
    invalid_executor.register(ExecutorProvider("invalid", lambda _context: object()))
    with pytest.raises(ConfigurationError, match="invalid adapter"):
        invalid_executor.resolve("invalid", **resolve)

    bad_entry_point = SimpleNamespace(
        name="broken",
        value="broken:provider",
        load=lambda: (_ for _ in ()).throw(ImportError("unavailable")),
    )
    entry_points = SimpleNamespace(select=lambda **_kwargs: [bad_entry_point])
    monkeypatch.setattr("omf.executors.registry.metadata.entry_points", lambda: entry_points)
    with pytest.raises(ConfigurationError, match="could not be loaded"):
        ExecutorRegistry().discover()

    wrong_entry_point = SimpleNamespace(
        name="declared-name",
        value="wrong:provider",
        load=lambda: ExecutorProvider("different-name", lambda _context: LocalExecutor()),
    )
    entry_points = SimpleNamespace(select=lambda **_kwargs: [wrong_entry_point])
    with pytest.raises(ConfigurationError, match="does not match"):
        ExecutorRegistry().discover()


def test_local_executor_success_failure_logs_and_reconcile(tmp_path):
    executor = LocalExecutor()
    success_dir = tmp_path / "success"
    plan = executor.plan(
        argv=[
            "python3",
            "-c",
            "import os,pathlib; pathlib.Path(os.environ['OMF_RESULT_FILE']).write_text('{}')",
        ],
        run_dir=success_dir,
        cwd=tmp_path,
    )
    execution_id = executor.submit(plan)
    process = executor._processes[execution_id]
    process.wait(timeout=5)
    assert executor.status(execution_id).state == "succeeded"
    assert executor.logs(execution_id) == (
        success_dir / "stdout.log",
        success_dir / "stderr.log",
    )
    assert executor.read_logs(execution_id) == ("", "")
    with pytest.raises(ValueError, match="positive"):
        executor.read_logs(execution_id, tail_bytes=0)
    recovered = LocalExecutor()
    assert recovered.reconcile(success_dir) == execution_id
    assert recovered.status(execution_id).state == "succeeded"

    failure_dir = tmp_path / "failure"
    failed = executor.submit(
        executor.plan(
            argv=["python3", "-c", "raise SystemExit(3)"], run_dir=failure_dir, cwd=tmp_path
        )
    )
    executor._processes[failed].wait(timeout=5)
    assert executor.status(failed).exit_code == 3


def test_local_executor_rejects_an_ambiguous_launch_record(tmp_path):
    run_dir = tmp_path / "ambiguous"
    run_dir.mkdir()
    (run_dir / "execution.json").write_text('{"id":"uncertain","state":"launching","started":0}')

    with pytest.raises(IntegrityError, match="launch outcome is indeterminate"):
        LocalExecutor().recover(run_dir)


def test_local_executor_recovers_a_completed_launch_before_pid_persistence(tmp_path):
    run_dir = tmp_path / "completed"
    run_dir.mkdir()
    (run_dir / "execution.json").write_text('{"id":"completed-id","state":"launching","started":0}')
    (run_dir / "completion.json").write_text('{"exitCode":0,"reason":"exit:0","finished":1}')
    (run_dir / "result.json").write_text("{}")
    executor = LocalExecutor()

    assert executor.recover(run_dir) == "completed-id"
    assert executor.status("completed-id").state == "succeeded"


def test_local_executor_enforces_timeout_without_controller_and_records_plain_command(tmp_path):
    executor = LocalExecutor()
    timed = executor.submit(
        executor.plan(
            argv=["python3", "-c", "import time; time.sleep(30)"],
            run_dir=tmp_path / "timed",
            cwd=tmp_path,
            timeout=0.1,
            requires_result=False,
        )
    )
    executor._processes[timed].wait(timeout=10)
    recovered = LocalExecutor()
    recovered.reconcile(tmp_path / "timed")
    status = recovered.status(timed)
    assert status.state == "failed"
    assert status.reason == "timeout"

    plain = executor.submit(
        executor.plan(
            argv=["python3", "-c", "pass"],
            run_dir=tmp_path / "plain",
            cwd=tmp_path,
            requires_result=False,
        )
    )
    executor._processes[plain].wait(timeout=5)
    assert executor.status(plain).state == "succeeded"


def test_local_executor_attests_executable_under_network_wrapper(tmp_path, monkeypatch):
    executor = LocalExecutor()
    monkeypatch.setattr(executor, "_network_namespace_available", lambda _path=None: True)
    environment = executor.prepare_environment(
        argv=["python3", "-c", "pass"],
        cwd=tmp_path,
        dependency=DependencyLock("requirements.lock", "sha256:test", b""),
        deny_network=True,
    )
    environment["executables"][0]["digest"] = "sha256:" + "0" * 64
    plan = executor.plan(
        argv=environment["command"],
        run_dir=tmp_path / "attested",
        cwd=tmp_path,
        deny_network=True,
        requires_result=False,
        environment=environment,
    )
    assert plan.argv[0] == environment["executables"][0]["path"]
    assert plan.metadata["executables"] == environment["executables"]

    execution_id = executor.submit(plan)
    for _ in range(100):
        status = executor.status(execution_id)
        if status.state != "running":
            break
        time.sleep(0.01)
    assert status.state == "failed"
    assert status.reason == "executable-digest-mismatch"


def test_local_environment_resolves_relative_path_entries(tmp_path, monkeypatch):
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    executable = binary_directory / "tool"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "bin")

    environment = LocalExecutor().prepare_environment(
        argv=["tool"],
        cwd=tmp_path,
        dependency=DependencyLock("requirements.lock", "sha256:test", b""),
    )
    assert environment["command"][0] == str(executable.resolve())
    assert environment["executables"][0]["path"] == str(executable.resolve())


def test_local_environment_rejects_nonempty_opaque_dependency_lock(tmp_path):
    with pytest.raises(CapabilityError, match="cannot realize a non-empty dependency lock"):
        LocalExecutor().prepare_environment(
            argv=["python3", "-c", "pass"],
            cwd=tmp_path,
            dependency=DependencyLock(
                "environment.lock", "sha256:" + "1" * 64, b"\x00provider-specific\xff"
            ),
        )


def test_local_environment_captures_python_runtime_and_distribution_inventory(tmp_path):
    environment = LocalExecutor().prepare_environment(
        argv=[sys.executable, "-c", "pass"],
        cwd=tmp_path,
        dependency=DependencyLock("requirements.lock", "sha256:test", b""),
    )

    runtime = environment["runtime"]
    assert runtime["system"]
    assert runtime["machine"]
    assert runtime["python"]["version"]
    assert runtime["python"]["implementation"]
    assert runtime["python"]["distributions"]
    assert environment["environmentPolicy"]["inherited"] == ["HOME", "LANG", "PATH", "TZ"]


def test_executor_log_reads_are_tail_bounded(tmp_path):
    executor = LocalExecutor()
    run_dir = tmp_path / "logs"
    execution_id = executor.submit(
        executor.plan(
            argv=[
                "python3",
                "-c",
                "print('0123456789'); import sys; print('abcdefghij', file=sys.stderr)",
            ],
            run_dir=run_dir,
            cwd=tmp_path,
            requires_result=False,
        )
    )
    executor._processes[execution_id].wait(timeout=5)

    stdout, stderr = executor.read_logs(execution_id, tail_bytes=5)
    assert stdout == "6789\n"
    assert stderr == "ghij\n"


def test_executor_log_error_replacement_remains_byte_bounded(tmp_path):
    executor = LocalExecutor()
    run_dir = tmp_path / "binary-logs"
    execution_id = executor.submit(
        executor.plan(
            argv=["python3", "-c", "import sys; sys.stdout.buffer.write(b'\\xff' * 10000)"],
            run_dir=run_dir,
            cwd=tmp_path,
            requires_result=False,
        )
    )
    executor._processes[execution_id].wait(timeout=5)

    stdout, _stderr = executor.read_logs(execution_id, tail_bytes=4096)
    assert stdout
    assert len(stdout.encode()) <= 4096


def test_local_executor_bounds_log_files_without_limiting_artifacts(tmp_path):
    executor = LocalExecutor()
    run_dir = tmp_path / "bounded-logs"
    artifact = run_dir / "model.bin"
    execution_id = executor.submit(
        executor.plan(
            argv=[
                "python3",
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "Path(sys.argv[1]).write_bytes(b'a' * 1500000); "
                    "print('x' * 1500000); print('y' * 1500000, file=sys.stderr)"
                ),
                str(artifact),
            ],
            run_dir=run_dir,
            cwd=tmp_path,
            requires_result=False,
        )
    )
    executor._processes[execution_id].wait(timeout=10)

    assert executor.status(execution_id).state == "succeeded"
    assert (run_dir / "stdout.log").stat().st_size == 1024 * 1024
    assert (run_dir / "stderr.log").stat().st_size == 1024 * 1024
    assert artifact.stat().st_size == 1500000
    assert (
        hashlib.sha256(artifact.read_bytes()).hexdigest()
        == hashlib.sha256(b"a" * 1500000).hexdigest()
    )


def test_builtin_preflight_rejects_unconsumed_binding_values():
    local = LocalExecutor(
        binding_resources={"cpu": 2, "memory": "1Gi", "accelerators": ["gpu"], "typo": 1},
        binding_spec={
            "placement": {"zone": "x"},
            "transport": {"kind": "x"},
            "extensions": {"x": True},
            "config": {
                "executor": {},
                "stores": {"artifacts": "remote"},
                "isolation": {"driver": "none"},
                "recovery": {"attempts": 2},
                "typo": True,
            },
        },
    )
    issues = local.preflight()
    assert len(issues) >= 8

    slurm = SlurmExecutor(
        shared_filesystem=True,
        binding_resources={"nodes": 0, "gpus": True, "unknown": 1},
        placement={"partition": "", "unknown": "x"},
        binding_spec={"transport": {"x": True}, "extensions": {"x": True}, "config": "bad"},
    )
    issues = slurm.preflight()
    assert any("positive integer" in issue for issue in issues)
    assert any("non-empty string" in issue for issue in issues)


def test_slurm_and_kubernetes_adapter_lifecycle(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "sbatch":
            return SimpleNamespace(stdout="42;cluster\n", returncode=0, stderr=b"")
        if argv[0] == "sacct":
            return SimpleNamespace(stdout="COMPLETED\n", returncode=0, stderr=b"")
        if "cluster-info" in argv:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if "get" in argv:
            return SimpleNamespace(stdout='{"status":{"succeeded":1}}', returncode=0, stderr=b"")
        if "logs" in argv:
            return SimpleNamespace(returncode=0, stdout=b"out", stderr=b"err")
        return SimpleNamespace(returncode=0, stdout="", stderr=b"")

    monkeypatch.setattr("omf.executors.slurm.subprocess.run", run)
    monkeypatch.setattr("omf.executors.kubernetes.subprocess.run", run)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tool")
    slurm = SlurmExecutor()
    slurm_plan = slurm.plan(
        argv=["python3", "train.py"],
        run_dir=tmp_path / "slurm",
        cwd=tmp_path,
        resources={"nodes": 2, "tasks": 8, "gpus": 8},
    )
    assert slurm.submit(slurm_plan) == "42"
    assert slurm.status("42").state == "succeeded"
    slurm.cancel("42")
    assert slurm.logs("42")[0].name == "slurm-42.out"

    image = "registry/model@sha256:" + "a" * 64
    kube = KubernetesExecutor(context="site")
    assert kube.preflight() == []
    plan = kube.plan(
        argv=["python3", "train.py"],
        run_dir=tmp_path / "kube",
        cwd=tmp_path,
        image=image,
        name="training",
    )
    assert kube.submit(plan) == "training"
    assert kube.status("training").state == "succeeded"
    assert kube.logs("training")[0].read_bytes() == b"out"
    kube.cancel("training")
    jobset = kube.plan(
        argv=["ignored"],
        run_dir=tmp_path / "jobset",
        cwd=tmp_path,
        image=image,
        roles=[{"name": "trainer"}],
    )
    assert jobset.metadata["resource"]["kind"] == "JobSet"


def test_slurm_module_transport_plan_is_explicit(tmp_path):
    executor = SlurmExecutor(
        shared_filesystem=True,
        binding_resources={"gpus": 4},
        placement={"partition": "gpu"},
    )
    plan = executor.plan(
        argv=["python3", "train.py"],
        run_dir=tmp_path / "run",
        cwd=tmp_path,
        timeout=61,
        requires_result=True,
    )
    script = plan.metadata["script"]
    assert script.index("#SBATCH --gpus=4") < script.index("set -eu")
    assert "#SBATCH --partition='gpu'" in script
    assert "#SBATCH --time=2" in script
    assert "OMF_REQUEST_FILE" in script
    assert "OMF_RESULT_FILE" in script
    assert "protocol:omf.module/v1" in executor.capabilities
    executor.attach("42", tmp_path / "run")

    with pytest.raises(RuntimeError, match="shared filesystem"):
        SlurmExecutor().plan(
            argv=["x"], run_dir=tmp_path / "missing", cwd=tmp_path, requires_result=True
        )
    with pytest.raises(RuntimeError, match="network denial"):
        executor.plan(argv=["x"], run_dir=tmp_path / "denied", cwd=tmp_path, deny_network=True)
