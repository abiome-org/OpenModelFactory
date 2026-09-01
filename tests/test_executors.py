import shutil
from types import SimpleNamespace

import pytest
from omf.executors.kubernetes import KubernetesExecutor
from omf.executors.local import LocalExecutor
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
