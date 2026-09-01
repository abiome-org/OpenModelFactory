import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from omf.config import ProjectPaths, bootstrap
from omf.database import AliasRepository
from omf.errors import CapabilityError, IntegrityError
from omf.executors import (
    MODULE_PROTOCOL_CAPABILITIES,
    ExecutorContext,
    ExecutorProvider,
    ExecutorRegistry,
    LocalExecutor,
)
from omf.factory import Factory


def _project(tmp_path: Path) -> ProjectPaths:
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "omf.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "Project",
                "metadata": {"name": "test-project", "namespace": "local/test-project"},
                "spec": {"owners": ["local-user"], "extensions": {}},
            }
        )
    )
    (root / "bindings").mkdir()
    binding = yaml.safe_load(Path("bindings/local.yaml").read_text())
    binding["metadata"]["namespace"] = "local/test-project"
    (root / "bindings/local.yaml").write_text(yaml.safe_dump(binding))
    shutil.copytree(Path("modules"), root / "modules")
    shutil.copytree(Path("workloads"), root / "workloads")
    (root / "data").mkdir()
    shutil.copy(Path("data/fixtures/numbers.jsonl"), root / "data/numbers.jsonl")
    return ProjectPaths(root)


def test_clean_clone_to_signed_release_and_edge_deployment(tmp_path):
    paths = _project(tmp_path)
    assert bootstrap(paths, plan=True)["actions"]
    assert bootstrap(paths)["ready"]
    assert bootstrap(paths)["actions"] == []
    with Factory(paths) as factory:
        assert factory.doctor()["ready"]
        factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        factory.add_store("secondary", driver="filesystem", endpoint=".omf/secondary-store")
        planned = factory.sync("dataset/example-numbers", destination="secondary", plan=True)
        assert planned["plan"]["bytes"] > 0
        assert factory.sync("dataset/example-numbers", destination="secondary")["committed"]
        assert (
            factory.sync("dataset/example-numbers", destination="secondary", plan=True)["plan"][
                "missingChunks"
            ]
            == []
        )
        assert factory.validate_module(paths.root / "modules/examples/statistical/module.yaml")[
            "valid"
        ]
        assert (
            factory.test_module(paths.root / "modules/examples/statistical/module.yaml")["passed"]
            == 1
        )
        run = factory.run(
            paths.root / "workloads/example-statistical.yaml",
            paths.root / "bindings/local.yaml",
        )
        assert run["state"] == "Succeeded"
        assert run["outputs"]["train.mean"] == 3.0
        evaluation = factory.evaluate(f"run/{run['runId']}")
        assert evaluation["spec"]["scores"]["passed"]
        run_status = factory.run_status(run["runId"])
        scan_path = paths.root / "vulnerability-report.yaml"
        scan_path.write_text(
            yaml.safe_dump(
                {
                    "scanner": {"name": "test-scanner", "version": "1"},
                    "databaseRevision": "test-db-1",
                    "generatedAt": "2026-09-01T00:00:00Z",
                    "subjects": [
                        run["outputs"]["train.model"],
                        *run_status["execution"]["digests"]["modules"].values(),
                    ],
                    "findings": [],
                    "waivers": [],
                }
            )
        )
        with pytest.raises(IntegrityError, match="vulnerabilities"):
            factory.create_release(
                run["runId"],
                name="unscanned",
                intended_use="test",
                promote=True,
                approvals=["independent-reviewer"],
            )
        assert all(
            item["metadata"]["name"] != "unscanned"
            for item in factory.list_resources(kind="Release")
        )
        release = factory.create_release(
            run["runId"],
            name="release-one",
            intended_use="test",
            promote=True,
            approvals=["independent-reviewer"],
            vulnerability_report=scan_path,
        )
        assert release["spec"]["extensions"]["promotionDecision"]["outcome"] == "allow"
        assert release["spec"]["extensions"]["manifest"]["vulnerabilities"]["status"] == "passed"
        assert release["spec"]["extensions"]["manifest"]["sbom"]["spdxVersion"] == "SPDX-2.3"
        assert AliasRepository(factory.db).get("candidate")[1] == release["metadata"]["revision"]
        release_uri = factory._resource_uri(release)
        release_lineage = factory.lineage_query(release_uri)
        assert any(edge["source"] == f"run:{run['runId']}" for edge in release_lineage)
        assert any(edge["source"].startswith("artifact:sha256:") for edge in release_lineage)
        deployment = {
            "apiVersion": "omf.dev/v1alpha1",
            "kind": "DeploymentSpec",
            "metadata": {"name": "edge-demo", "namespace": "local/test-project"},
            "spec": {
                "releaseRef": "release/release-one",
                "runtime": "omf.module/v1",
                "routing": {},
                "extensions": {"form": "edge"},
            },
        }
        deployment_path = paths.root / "deployment.yaml"
        deployment_path.write_text(yaml.safe_dump(deployment))
        edge_deployment = factory.deploy(deployment_path)
        assert edge_deployment["state"] == "packaged"
        assert any(
            edge["source"] == release_uri
            for edge in factory.lineage_query(factory._resource_uri(edge_deployment["deployment"]))
        )

        service = {**deployment, "metadata": {**deployment["metadata"], "name": "service-demo"}}
        service["spec"] = {
            **deployment["spec"],
            "extensions": {
                "form": "service",
                "command": ["python3", "-c", "pass"],
            },
        }
        service_path = paths.root / "service-deployment.yaml"
        service_path.write_text(yaml.safe_dump(service))
        assert factory.deploy(service_path)["state"] == "running"
        for _ in range(100):
            service_status = factory.deployment_status("service-demo")
            if service_status["status"]["state"] != "running":
                break
            time.sleep(0.02)
        assert service_status["status"]["state"] == "succeeded"
        assert service_status["status"]["executor"] == "local"
        first_deployment_revision = service_status["status"]["deploymentRevision"]

        service["spec"]["extensions"]["command"] = ["python3", "-c", "print('revision-two')"]
        service_path.write_text(yaml.safe_dump(service))
        factory.deploy(service_path)
        for _ in range(100):
            service_status = factory.deployment_status("service-demo")
            if service_status["status"]["state"] != "running":
                break
            time.sleep(0.02)
        second_deployment_revision = service_status["status"]["deploymentRevision"]
        assert second_deployment_revision != first_deployment_revision
        rolled_back = factory.rollback_deployment(
            "service-demo", expected_version=service_status["statusVersion"]
        )
        assert rolled_back["status"]["deploymentRevision"] == first_deployment_revision
        for _ in range(100):
            rolled_back = factory.deployment_status("service-demo")
            if rolled_back["status"]["state"] != "running":
                break
            time.sleep(0.02)
        assert rolled_back["status"]["state"] == "succeeded"

        rolled_forward = factory.deploy(service_path)
        assert rolled_forward["deployment"]["metadata"]["revision"] == second_deployment_revision
        for _ in range(100):
            rolled_forward = factory.deployment_status("service-demo")
            if rolled_forward["status"]["state"] != "running":
                break
            time.sleep(0.02)
        assert rolled_forward["status"]["state"] == "succeeded"

        service["metadata"]["name"] = "cancel-demo"
        service["spec"]["extensions"]["command"] = [
            "python3",
            "-c",
            "import time; time.sleep(30)",
        ]
        service_path.write_text(yaml.safe_dump(service))
        factory.deploy(service_path)
        assert factory.cancel_deployment("cancel-demo")["status"]["state"] == "canceled"

    with Factory(paths) as restarted:
        assert restarted.deployment_status("service-demo")["status"]["state"] == "succeeded"


def test_non_local_binding_is_not_silently_executed_locally(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    binding = yaml.safe_load((paths.root / "bindings/local.yaml").read_text())
    binding["metadata"]["name"] = "cluster"
    binding["spec"]["executor"] = "slurm"
    binding_path = paths.root / "bindings/cluster.yaml"
    binding_path.write_text(yaml.safe_dump(binding))

    with Factory(paths) as factory:
        with pytest.raises(CapabilityError, match="not ready") as failure:
            factory.run(paths.root / "workloads/example-statistical.yaml", binding_path)
        assert "protocol:omf.module/v1" in failure.value.details["missingCapabilities"]
        assert factory.list_resources(kind="Run") == []
        assert list(paths.runs.iterdir()) == []


def test_injected_executor_runs_unchanged_workload(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    binding = yaml.safe_load((paths.root / "bindings/local.yaml").read_text())
    binding["metadata"]["name"] = "remote"
    binding["spec"]["executor"] = "test-remote"
    binding_path = paths.root / "bindings/remote.yaml"
    binding_path.write_text(yaml.safe_dump(binding))

    class RecordingExecutor(LocalExecutor):
        def plan(self, **kwargs):
            self.planned = True
            return super().plan(**kwargs)

    created: list[RecordingExecutor] = []

    def create(_context: ExecutorContext) -> LocalExecutor:
        executor = RecordingExecutor()
        executor.planned = False
        created.append(executor)
        return executor

    registry = ExecutorRegistry()
    registry.register(
        ExecutorProvider(
            "test-remote",
            create,
            capabilities=MODULE_PROTOCOL_CAPABILITIES | frozenset({"isolation:network-deny"}),
        )
    )
    with Factory(paths, executors=registry) as factory:
        factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        result = factory.run(paths.root / "workloads/example-statistical.yaml", binding_path)
    assert result["state"] == "Succeeded"
    assert len(created) == 1
    assert created[0].planned
