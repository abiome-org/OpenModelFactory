import fcntl
import hashlib
import json
import shutil
import subprocess
import time
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from omf.artifacts import ArtifactBuilder
from omf.config import ProjectPaths, bootstrap
from omf.database import AliasRepository
from omf.errors import CapabilityError, ConflictError, IntegrityError, ValidationError
from omf.executors import (
    MODULE_PROTOCOL_CAPABILITIES,
    ExecutorContext,
    ExecutorProvider,
    ExecutorRegistry,
    LocalExecutor,
)
from omf.factory import Factory
from omf.modules import load_manifest
from omf.workloads import project_workload


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
    workload = yaml.safe_load((root / "workloads/example-statistical.yaml").read_text())
    workload["metadata"]["namespace"] = "local/test-project"
    (root / "workloads/example-statistical.yaml").write_text(yaml.safe_dump(workload))
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
        assert factory.operations.get(run["operationId"])["state"] == "succeeded"
        assert factory.operations.get(run["operationId"])["result"]["runId"] == run["runId"]
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
        conflicting_evaluation = deepcopy(evaluation)
        conflicting_evaluation["metadata"] = {
            "name": "conflicting-evaluation",
            "namespace": "local/test-project",
        }
        conflicting_evaluation["spec"]["scores"]["passed"] = False
        conflicting_evaluation["spec"]["extensions"]["passed"] = False
        with pytest.raises(ValidationError, match="factory coordinator"):
            factory.apply_resource(conflicting_evaluation)
        release = factory.create_release(
            run["runId"],
            name="release-one",
            intended_use="test",
            promote=True,
            approvals=["independent-reviewer"],
            vulnerability_report=scan_path,
            evaluation_ref=evaluation["metadata"]["revision"],
        )
        assert release["spec"]["extensions"]["promotionDecision"]["outcome"] == "allow"
        assert release["spec"]["extensions"]["manifest"]["vulnerabilities"]["status"] == "passed"
        assert release["spec"]["extensions"]["manifest"]["sbom"]["spdxVersion"] == "SPDX-2.3"
        assert release["spec"]["extensions"]["manifest"]["compatibility"]["passed"] is True
        assert (
            release["spec"]["extensions"]["manifest"]["compatibility"]["evaluationRevision"]
            == evaluation["metadata"]["revision"]
        )
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
        assert factory.operations.list() == []
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
    assert len(created) == 2
    assert not created[0].planned
    assert created[1].planned


def test_opaque_dependency_lock_reaches_provider_without_core_interpretation(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    module_path = paths.root / "modules/examples/statistical/module.yaml"
    lock = b"\x00provider-specific\xff\n"
    (module_path.parent / "requirements.lock").write_bytes(lock)
    module = yaml.safe_load(module_path.read_text())
    module["spec"]["environment"]["dependencyDigest"] = "sha256:" + hashlib.sha256(lock).hexdigest()
    module_path.write_text(yaml.safe_dump(module))
    binding = yaml.safe_load((paths.root / "bindings/local.yaml").read_text())
    binding["spec"]["executor"] = "opaque-lock"
    binding_path = paths.root / "bindings/opaque-lock.yaml"
    binding_path.write_text(yaml.safe_dump(binding))
    observed = []

    class OpaqueLockExecutor(LocalExecutor):
        def prepare_environment(self, *, argv, cwd, dependency, deny_network=False):
            del cwd, deny_network
            observed.append(dependency)
            return {
                "command": list(argv),
                "dependencyDigest": dependency.digest,
                "digest": "sha256:" + "2" * 64,
            }

    registry = ExecutorRegistry()
    registry.register(
        ExecutorProvider(
            "opaque-lock",
            lambda _context: OpaqueLockExecutor(),
            capabilities=MODULE_PROTOCOL_CAPABILITIES | frozenset({"isolation:network-deny"}),
        )
    )
    with Factory(paths, executors=registry) as factory:
        validation = factory.validate_module(module_path)
        report = factory.executor_preflight(
            binding_path, workload_path=paths.root / "workloads/example-statistical.yaml"
        )

    assert validation["dependencyLock"]["size"] == len(lock)
    assert report["ready"]
    assert len(observed) == 2
    assert all(item.relative_path == "requirements.lock" for item in observed)
    assert all(item.contents == lock for item in observed)
    assert all(
        item.digest == module["spec"]["environment"]["dependencyDigest"] for item in observed
    )


@pytest.mark.parametrize(
    ("descriptor", "message"),
    [
        (
            {"command": [], "dependencyDigest": "sha256:x", "digest": "sha256:" + "0" * 64},
            "command argv",
        ),
        (
            {
                "command": ["python3"],
                "dependencyDigest": "sha256:x",
                "digest": "sha256:" + "0" * 64,
            },
            "changed the dependency lock",
        ),
        (
            {"command": ["python3"], "dependencyDigest": "MATCH", "digest": "invalid"},
            "canonical digest",
        ),
        (
            {
                "command": ["python3"],
                "dependencyDigest": "MATCH",
                "digest": "sha256:" + "0" * 64,
                "opaque": b"not-json",
            },
            "canonical JSON",
        ),
    ],
)
def test_provider_environment_descriptor_is_centrally_validated(tmp_path, descriptor, message):
    paths = _project(tmp_path)
    bootstrap(paths)
    module_path = paths.root / "modules/examples/statistical/module.yaml"

    class DescriptorExecutor(LocalExecutor):
        def prepare_environment(self, **_kwargs):
            return {
                key: (
                    yaml.safe_load(module_path.read_text())["spec"]["environment"][
                        "dependencyDigest"
                    ]
                    if value == "MATCH"
                    else value
                )
                for key, value in descriptor.items()
            }

    with Factory(paths) as factory:
        manifest, code_root = load_manifest(module_path, paths.root)
        with pytest.raises(IntegrityError, match=message):
            factory._prepare_module_environment(DescriptorExecutor(), manifest, code_root)


def test_run_pins_dataset_revision_before_execution(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        first = factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        workload = yaml.safe_load((paths.root / "workloads/example-statistical.yaml").read_text())
        pinned = factory._pin_stage_inputs(project_workload(workload).stages)

        (paths.root / "data/numbers.jsonl").write_text('{"value": 99}\n')
        second = factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )

        assert first["metadata"]["revision"] != second["metadata"]["revision"]
        assert (
            pinned["dataset/example-numbers"]["metadata"]["revision"]
            == first["metadata"]["revision"]
        )


def test_run_rejects_non_copy_dataset_before_allocation(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="register",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        with pytest.raises(CapabilityError, match="only copied dataset snapshots"):
            factory.run(
                paths.root / "workloads/example-statistical.yaml",
                paths.root / "bindings/local.yaml",
            )
        assert factory.list_resources(kind="Run") == []
        assert list(paths.runs.iterdir()) == []


def test_module_manifest_revision_changes_admitted_source_identity(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    manifest_path = paths.root / "modules/examples/statistical/module.yaml"
    with Factory(paths) as factory:
        first = factory.validate_module(manifest_path)
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["spec"]["provenance"]["sourceRef"] = "repository:modules/examples/statistical-v2"
        manifest_path.write_text(yaml.safe_dump(manifest))
        second = factory.validate_module(manifest_path)

    assert first["artifactManifest"] != second["artifactManifest"]


def test_copied_dataset_revision_is_relocatable(tmp_path):
    revisions = []
    for name in ("first", "second"):
        project_parent = tmp_path / name
        project_parent.mkdir()
        paths = _project(project_parent)
        bootstrap(paths)
        with Factory(paths) as factory:
            resource = factory.add_data(
                paths.root / "data/numbers.jsonl",
                name="example-numbers",
                mode="copy",
                rights={"license": "CC0-1.0", "trainingAllowed": True},
            )
            revisions.append(resource["metadata"]["revision"])
            assert resource["spec"]["extensions"]["source"].startswith("sha256:")
    assert revisions[0] == revisions[1]


def test_run_rejects_corrupted_dataset_before_allocation(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        resource = factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        digest = resource["spec"]["extensions"]["artifact"]["chunks"][0]["digest"]
        digest_hex = digest.removeprefix("sha256:")
        (paths.store / "blobs" / digest_hex[:2] / digest_hex).write_bytes(b"corrupt")

        with pytest.raises(IntegrityError, match="dataset artifact"):
            factory.run(
                paths.root / "workloads/example-statistical.yaml",
                paths.root / "bindings/local.yaml",
            )
        assert factory.list_resources(kind="Run") == []
        assert list(paths.runs.iterdir()) == []


def test_workload_preflight_checks_environment_and_binding_semantics(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    workload_path = paths.root / "workloads/example-statistical.yaml"
    binding_path = paths.root / "bindings/local.yaml"
    module_path = paths.root / "modules/examples/statistical/module.yaml"

    module = yaml.safe_load(module_path.read_text())
    module["spec"]["entryPoint"]["command"][0] = "missing-executable"
    module_path.write_text(yaml.safe_dump(module))
    with Factory(paths) as factory:
        report = factory.executor_preflight(binding_path, workload_path=workload_path)
    assert not report["ready"]
    assert any("unavailable" in issue for issue in report["issues"])

    module["spec"]["entryPoint"]["command"][0] = "python3"
    module_path.write_text(yaml.safe_dump(module))
    binding = yaml.safe_load(binding_path.read_text())
    binding["spec"]["placement"] = {"zone": "ignored"}
    binding_path.write_text(yaml.safe_dump(binding))
    with Factory(paths) as factory:
        report = factory.executor_preflight(binding_path, workload_path=workload_path)
    assert not report["ready"]
    assert any("placement" in issue for issue in report["issues"])


def test_model_neutral_from_scratch_golden_path(tmp_path):
    paths = _project(tmp_path)
    workload_path = paths.root / "workloads/example-from-scratch.yaml"
    workload = yaml.safe_load(workload_path.read_text())
    workload["metadata"]["namespace"] = "local/test-project"
    workload_path.write_text(yaml.safe_dump(workload))
    model_package = yaml.safe_load(Path("model-packages/example-affine.yaml").read_text())
    model_package["metadata"]["namespace"] = "local/test-project"
    evaluation_spec = yaml.safe_load(Path("evaluations/example-affine.yaml").read_text())
    evaluation_spec["metadata"]["namespace"] = "local/test-project"
    mix = yaml.safe_load(Path("mixes/example-affine.yaml").read_text())
    mix["metadata"]["namespace"] = "local/test-project"
    bootstrap(paths)
    with Factory(paths) as factory:
        package_resource = factory.apply_resource(model_package)
        suite_resource = factory.apply_resource(evaluation_spec)
        factory.add_data(
            Path("data/fixtures/affine.jsonl").resolve(),
            name="example-affine",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        mix_resource = factory.apply_resource(mix)
        result = factory.run(workload_path, paths.root / "bindings/local.yaml")
        state = json.loads((paths.runs / result["runId"] / "state.json").read_text())
        state["digests"]["modules"]["train"] = "sha256:" + "0" * 64
        (paths.runs / result["runId"] / "state.json").write_text(json.dumps(state))
        (paths.root / "modules/examples/affine-regression/main.py").write_text(
            "raise RuntimeError('live source must not execute')\n"
        )
        evaluation = factory.evaluate(f"run/{result['runId']}")
        experiment = factory.create_experiment(
            name="affine-self-check",
            baseline_ref=factory._resource_uri(evaluation),
            candidate_ref=factory._resource_uri(evaluation),
            metric="training-loss",
            direction="minimize",
        )
        admitted_evaluation_refs = factory._run_resource(result["runId"])["spec"]["extensions"][
            "evaluationRefs"
        ]
        admitted_mix_ref = factory._run_resource(result["runId"])["spec"]["extensions"]["mixRef"]
        checkpoints = factory.list_resources(kind="Checkpoint")
        model_manifest = factory.local_store.read_manifest(result["outputs"]["train.model"])
        restored = tmp_path / "restored-model"
        ArtifactBuilder(factory.local_store).restore(model_manifest, restored)

    assert result["state"] == "Succeeded"
    assert result["outputs"]["train.loss"] < 1e-6
    assert result["outputs"]["evaluate.passed"] is True
    assert result["outputs"]["train.model"].startswith("sha256:")
    assert result["outputs"]["train.checkpoint"].startswith("sha256:")
    assert len(checkpoints) == 1
    assert checkpoints[0]["spec"]["artifactRef"] == result["outputs"]["train.checkpoint"]
    assert checkpoints[0]["spec"]["components"]["protocol-state"].startswith("sha256:")
    assert checkpoints[0]["spec"]["replay"] == {
        "status": "not-claimed",
        "reason": "sampler-state-not-observed",
    }
    assert json.loads((restored / "payload").read_text()) == result["outputs"]["train.modelState"]
    assert evaluation["spec"]["extensions"]["compatibilityPassed"] is True
    assert evaluation["spec"]["scores"]["training-loss"] < 1e-6
    assert experiment["spec"]["decision"] == "tie"
    assert factory._resource_uri(suite_resource) in admitted_evaluation_refs
    assert admitted_mix_ref == factory._resource_uri(mix_resource)
    assert evaluation["spec"]["extensions"]["modelPackageRef"] == factory._resource_uri(
        package_resource
    )


def test_model_package_admission_rejects_unexecutable_contracts(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    workload = yaml.safe_load((paths.root / "workloads/example-from-scratch.yaml").read_text())
    stages = project_workload(workload).stages
    base = yaml.safe_load(Path("model-packages/example-affine.yaml").read_text())
    base["metadata"]["namespace"] = "local/test-project"

    with Factory(paths) as factory:
        optimized = deepcopy(base)
        optimized["metadata"]["name"] = "optimized"
        optimized["spec"]["adapters"]["optimized"] = [
            deepcopy(optimized["spec"]["adapters"]["inferenceReference"])
        ]
        factory.apply_resource(optimized)
        with pytest.raises(ValidationError, match="optimized model adapters"):
            factory._pin_model_package("modelpackage/optimized", stages)

        tolerance = deepcopy(base)
        tolerance["metadata"]["name"] = "invalid-tolerance"
        tolerance["spec"]["compatibilityVectors"][0]["tolerances"]["prediction"]["absolute"] = -1
        factory.apply_resource(tolerance)
        with pytest.raises(ValidationError, match="finite and non-negative"):
            factory._pin_model_package("modelpackage/invalid-tolerance", stages)

        signature = deepcopy(base)
        signature["metadata"]["name"] = "invalid-signature"
        signature["spec"]["signatures"]["state"] = {"type": "string"}
        factory.apply_resource(signature)
        with pytest.raises(ValidationError, match="must describe an object"):
            factory._pin_model_package("modelpackage/invalid-signature", stages)


def test_model_package_admission_rejects_adapter_and_vector_drift(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    workload = yaml.safe_load((paths.root / "workloads/example-from-scratch.yaml").read_text())
    stages = project_workload(workload).stages
    base = yaml.safe_load(Path("model-packages/example-affine.yaml").read_text())
    base["metadata"]["namespace"] = "local/test-project"

    with Factory(paths) as factory:
        with pytest.raises(IntegrityError, match="does not match the workload"):
            factory._pin_model_package(None, stages, "omf://stale/model-package")
        assert factory._pin_model_package(None, stages) is None
        with pytest.raises(ValidationError, match="modelpackage/<name>"):
            factory._pin_model_package("modelpackage/example@sha256:stale", stages)

        def rejected(name, mutate, match):
            package = deepcopy(base)
            package["metadata"]["name"] = name
            mutate(package)
            factory.apply_resource(package)
            with pytest.raises(ValidationError, match=match):
                factory._pin_model_package(f"modelpackage/{name}", stages)

        rejected(
            "external-contract-ref",
            lambda package: package["spec"]["signatures"]["input"].update(
                {"properties": {"input": {"$ref": "https://example.invalid/input.json"}}}
            ),
            "references",
        )
        rejected(
            "invalid-vector-input",
            lambda package: package["spec"]["compatibilityVectors"][0].update({"inputs": {}}),
            "model package input",
        )
        rejected(
            "invalid-vector-output",
            lambda package: package["spec"]["compatibilityVectors"][0].update({"expected": {}}),
            "model package output",
        )
        rejected(
            "invalid-tolerance-shape",
            lambda package: package["spec"]["compatibilityVectors"][0]["tolerances"].update(
                {"prediction": {"absolute": True}}
            ),
            "finite and non-negative",
        )
        rejected(
            "unknown-training-stage",
            lambda package: package["spec"]["adapters"]["trainingReference"].update(
                {"stage": "missing"}
            ),
            "unknown workload stage",
        )
        rejected(
            "training-operation-drift",
            lambda package: package["spec"]["adapters"]["trainingReference"].update(
                {"operation": "validate"}
            ),
            "does not match the workload stage",
        )
        rejected(
            "missing-state-output",
            lambda package: package["spec"]["adapters"]["inferenceReference"].pop("stateOutput"),
            "requires stage.output",
        )
        rejected(
            "invalid-state-output",
            lambda package: package["spec"]["adapters"]["inferenceReference"].update(
                {"stateOutput": "train.missing"}
            ),
            "not declared by the workload",
        )

        admitted = deepcopy(base)
        admitted["metadata"]["name"] = "expected-revision"
        resource = factory.apply_resource(admitted)
        reference = factory._resource_uri(resource)
        assert (
            factory._pin_model_package("modelpackage/expected-revision", stages, reference)
            == resource
        )


def test_resource_pinning_and_resolution_fail_closed(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    workload = yaml.safe_load((paths.root / "workloads/example-from-scratch.yaml").read_text())
    stages = project_workload(workload).stages
    evaluation = yaml.safe_load(Path("evaluations/example-affine.yaml").read_text())
    evaluation["metadata"]["namespace"] = "local/test-project"
    mix = yaml.safe_load(Path("mixes/example-affine.yaml").read_text())
    mix["metadata"]["namespace"] = "local/test-project"

    with Factory(paths) as factory:
        dataset = factory.add_data(
            Path("data/fixtures/affine.jsonl").resolve(),
            name="example-affine",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        suite = factory.apply_resource(evaluation)
        mix_resource = factory.apply_resource(mix)

        with pytest.raises(IntegrityError, match="dataset reference was not pinned"):
            factory._pin_stage_inputs(stages, {})
        with pytest.raises(IntegrityError, match="references do not match"):
            factory._pin_named_resources(
                ["evaluationspec/example-affine"], "evaluationspec/", "EvaluationSpec", []
            )
        with pytest.raises(ValidationError, match="reference must use"):
            factory._pin_named_resources(
                ["evaluation/example-affine"], "evaluationspec/", "EvaluationSpec"
            )
        assert factory._pin_named_resources(
            ["evaluationspec/example-affine"],
            "evaluationspec/",
            "EvaluationSpec",
            [factory._resource_uri(suite)],
        ) == [suite]
        with pytest.raises(IntegrityError, match="MixSpec does not match"):
            factory._pin_mix(None, {}, factory._resource_uri(mix_resource))
        with pytest.raises(ValidationError, match="not an admitted workload dataset"):
            factory._pin_mix("mixspec/example-affine", {})
        assert (
            factory._pin_mix(
                "mixspec/example-affine",
                {"dataset/example-affine": dataset},
                factory._resource_uri(mix_resource),
            )
            == mix_resource
        )

        assert factory._resolve_output_reference("literal", {}, stages) == "literal"
        with pytest.raises(IntegrityError, match="stage output reference is unavailable"):
            factory._resolve_output_reference("train.model", {}, stages)
        assert factory._resolve_stage_input(7, paths.runs / "x", {}) == 7
        with pytest.raises(IntegrityError, match="not pinned at admission"):
            factory._resolve_stage_input("dataset/missing", paths.runs / "x/y/z", {})

        target_root = paths.runs / "run" / "stages" / "train" / "inputs" / "dataset"
        materialized = factory._resolve_stage_input(
            "dataset/example-affine", target_root, {"dataset/example-affine": dataset}
        )
        assert materialized["manifestDigest"].startswith("sha256:")
        with pytest.raises(IntegrityError, match="target already exists"):
            factory._resolve_stage_input(
                "dataset/example-affine", target_root, {"dataset/example-affine": dataset}
            )

        assert factory._verify_stage_outputs({"value": 1})
        assert not factory._verify_stage_outputs({"artifact": "sha256:" + "0" * 64})
        with pytest.raises(IntegrityError, match="immutable result"):
            factory._run_result("missing", {})
        with pytest.raises(IntegrityError, match="identity is ambiguous"):
            factory._run_resource("missing")

    assert Factory._compatibility_equal(True, True, {})
    assert Factory._compatibility_equal(1.0, 1.001, {"absolute": 0.01})
    assert Factory._compatibility_equal([1, 2], [1, 2], {})
    assert not Factory._compatibility_equal([1], [1, 2], {})
    assert Factory._compatibility_equal({"x": 1}, {"x": 1}, {})
    assert not Factory._compatibility_equal({"x": 1}, {"y": 1}, {})
    assert not Factory._compatibility_equal("left", "right", {})


def test_experiment_rejects_different_evaluation_revisions(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        baseline = factory.apply_resource(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "EvaluationResult",
                "metadata": {"name": "baseline", "namespace": "local/test-project"},
                "spec": {
                    "evaluationRef": "run/baseline",
                    "scores": {"loss": 1.0},
                    "provenance": {},
                    "uncertainty": {},
                    "failures": [],
                    "extensions": {"evaluationRefs": ["omf://test/evaluationspec/a@sha256:a"]},
                },
            },
            _system=True,
        )
        candidate = factory.apply_resource(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "EvaluationResult",
                "metadata": {"name": "candidate", "namespace": "local/test-project"},
                "spec": {
                    "evaluationRef": "run/candidate",
                    "scores": {"loss": 0.5},
                    "provenance": {},
                    "uncertainty": {},
                    "failures": [],
                    "extensions": {"evaluationRefs": ["omf://test/evaluationspec/b@sha256:b"]},
                },
            },
            _system=True,
        )

        with pytest.raises(ValidationError, match="different evaluation revisions"):
            factory.create_experiment(
                name="invalid-comparison",
                baseline_ref=factory._resource_uri(baseline),
                candidate_ref=factory._resource_uri(candidate),
                metric="loss",
                direction="minimize",
            )

        candidate["spec"]["extensions"]["evaluationRefs"] = baseline["spec"]["extensions"][
            "evaluationRefs"
        ]
        for invalid in (True, "0.5"):
            candidate["spec"]["scores"]["loss"] = invalid
            replacement = factory.apply_resource(
                {
                    **candidate,
                    "metadata": {
                        "name": f"candidate-{str(invalid).lower().replace('.', '-')}",
                        "namespace": "local/test-project",
                    },
                },
                _system=True,
            )
            with pytest.raises(ValidationError, match="non-numeric"):
                factory.create_experiment(
                    name=f"invalid-{invalid!s}",
                    baseline_ref=factory._resource_uri(baseline),
                    candidate_ref=factory._resource_uri(replacement),
                    metric="loss",
                    direction="minimize",
                )


def test_pending_run_operation_executes_after_controller_restart(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        operation = factory.create_run_operation(
            paths.root / "workloads/example-statistical.yaml",
            paths.root / "bindings/local.yaml",
        )

    with Factory(paths) as restarted:
        completed = restarted.execute_run_operation(operation["id"])
        run_resource = restarted._run_resource(completed["result"]["runId"])

    assert completed["state"] == "succeeded"
    assert completed["result"]["runId"] == operation["id"]
    assert run_resource["spec"]["extensions"]["operationId"] == operation["id"]


def test_stale_running_operation_fails_closed_without_reexecution(tmp_path, monkeypatch):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        operation = factory.operations.create(
            "run",
            {
                "actor": factory.actor,
                "workload": "workloads/unused.yaml",
                "binding": "bindings/unused.yaml",
            },
        )
        operation = factory.operations.get(operation["id"])
        factory.operations.update(
            operation["id"], expected_version=operation["version"], state="running"
        )
        monkeypatch.setattr(
            factory,
            "_run_impl",
            lambda *_args, **_kwargs: pytest.fail("uncertain work must not be executed again"),
        )
        with pytest.raises(IntegrityError, match="automatic replay is disabled"):
            factory.execute_run_operation(operation["id"])
        failed = factory.operations.get(operation["id"])

    assert failed["state"] == "failed"
    assert failed["error"]["code"] == "indeterminate_execution"
    assert not failed["error"]["retryable"]


def test_running_operation_reconciles_immutable_completed_result(tmp_path, monkeypatch):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        operation = factory.create_run_operation(
            paths.root / "workloads/example-statistical.yaml",
            paths.root / "bindings/local.yaml",
        )
        apply_resource = factory.apply_resource

        def interrupt_completion(value, **kwargs):
            resource = apply_resource(value, **kwargs)
            if value.get("kind") == "RunResult":
                raise KeyboardInterrupt("controller stopped after immutable result publication")
            return resource

        with monkeypatch.context() as patch:
            patch.setattr(factory, "apply_resource", interrupt_completion)
            with pytest.raises(KeyboardInterrupt, match="controller stopped"):
                factory.execute_run_operation(operation["id"])
        assert factory.operations.get(operation["id"])["state"] == "running"

        completed = factory.execute_run_operation(operation["id"])
        status = factory.run_status(operation["id"])["status"]
        result_lineage = [
            edge
            for edge in factory.lineage.by_run(operation["id"])
            if edge.target == completed["result"]["resultRef"]
        ]
        terminal_events = factory.events.query(
            run_id=operation["id"], resource_uid=operation["id"], type="RunStateChanged"
        )

    assert completed["state"] == "succeeded"
    assert completed["result"]["runId"] == operation["id"]
    assert completed["result"]["outputs"]["train.mean"] == 3.0
    assert status["state"] == "Succeeded"
    assert len(result_lineage) == 1
    assert [event.data["state"] for event in terminal_events] == ["Succeeded"]


def test_run_operation_execution_lease_rejects_concurrent_worker(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        operation = factory.operations.create(
            "run", {"actor": factory.actor, "workload": "unused", "binding": "unused"}
        )
        lock_path = paths.state / "operations" / f"{operation['id']}.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(ConflictError, match="already executing"):
                factory.execute_run_operation(operation["id"])
        assert factory.operations.get(operation["id"])["state"] == "pending"


def test_queued_run_pins_manifest_outside_code_root(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    module_root = paths.root / "modules/examples/statistical"
    source_root = module_root / "src"
    source_root.mkdir()
    for name in ("main.py", "requirements.lock"):
        (module_root / name).replace(source_root / name)
    manifest_path = module_root / "module.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["spec"]["entryPoint"]["codeRoot"] = "src"
    manifest_path.write_text(yaml.safe_dump(manifest))

    with Factory(paths) as factory:
        factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        operation = factory.create_run_operation(
            paths.root / "workloads/example-statistical.yaml",
            paths.root / "bindings/local.yaml",
        )
        manifest["spec"]["provenance"]["sourceRef"] = "repository:changed-after-queue"
        manifest_path.write_text(yaml.safe_dump(manifest))

        with pytest.raises(IntegrityError, match="module source changed"):
            factory.execute_run_operation(operation["id"])
        assert factory.operations.get(operation["id"])["state"] == "failed"
        assert factory.list_resources(kind="Run") == []


def test_queued_run_uses_exact_dataset_revision_after_alias_advances(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        operation = factory.create_run_operation(
            paths.root / "workloads/example-statistical.yaml",
            paths.root / "bindings/local.yaml",
        )
        (paths.root / "data/numbers.jsonl").write_text('{"value": 99}\n')
        factory.add_data(
            paths.root / "data/numbers.jsonl",
            name="example-numbers",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )

        completed = factory.execute_run_operation(operation["id"])

    assert completed["result"]["outputs"]["train.mean"] == 3.0


def test_run_operation_records_admission_and_worker_failures(tmp_path, monkeypatch):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        invalid_kind = factory.operations.create("other", {"actor": factory.actor})
        with pytest.raises(ValidationError, match="not an executable pending run"):
            factory.execute_run_operation(invalid_kind["id"])

        wrong_actor = factory.operations.create("run", {"actor": "another-controller"})
        with pytest.raises(ValidationError, match="actor does not match"):
            factory.execute_run_operation(wrong_actor["id"])

        def operation():
            return factory.operations.create(
                "run",
                {
                    "actor": factory.actor,
                    "workload": "workloads/unused.yaml",
                    "binding": "bindings/unused.yaml",
                    "workloadDigest": "sha256:" + "0" * 64,
                    "bindingDigest": "sha256:" + "0" * 64,
                    "modulePackages": {},
                    "resources": {},
                },
            )

        admission_domain = operation()
        with monkeypatch.context() as patch:
            patch.setattr(
                factory,
                "_verify_run_request",
                lambda _request: (_ for _ in ()).throw(IntegrityError("admission domain")),
            )
            with pytest.raises(IntegrityError, match="admission domain"):
                factory.execute_run_operation(admission_domain["id"])
        assert factory.operations.get(admission_domain["id"])["error"]["code"] == "integrity_error"

        admission_generic = operation()
        with monkeypatch.context() as patch:
            patch.setattr(
                factory,
                "_verify_run_request",
                lambda _request: (_ for _ in ()).throw(RuntimeError("admission generic")),
            )
            with pytest.raises(RuntimeError, match="admission generic"):
                factory.execute_run_operation(admission_generic["id"])
        assert (
            factory.operations.get(admission_generic["id"])["error"]["code"]
            == "run_admission_error"
        )

        worker_domain = operation()
        with monkeypatch.context() as patch:
            patch.setattr(factory, "_verify_run_request", lambda _request: None)
            patch.setattr(
                factory,
                "_run_impl",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(ValidationError("worker domain")),
            )
            with pytest.raises(ValidationError, match="worker domain"):
                factory.execute_run_operation(worker_domain["id"])
        assert factory.operations.get(worker_domain["id"])["error"]["code"] == "validation_error"

        worker_generic = operation()
        with monkeypatch.context() as patch:
            patch.setattr(factory, "_verify_run_request", lambda _request: None)
            patch.setattr(
                factory,
                "_run_impl",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("worker generic")),
            )
            with pytest.raises(RuntimeError, match="worker generic"):
                factory.execute_run_operation(worker_generic["id"])
        assert factory.operations.get(worker_generic["id"])["error"]["code"] == "run_worker_error"


@pytest.mark.parametrize("failure", ["multiple", "state", "declaration"])
def test_checkpoint_publication_rejects_incomplete_stage_results(tmp_path, failure):
    paths = _project(tmp_path)
    bootstrap(paths)
    workload_path = paths.root / "workloads/example-from-scratch.yaml"
    workload = yaml.safe_load(workload_path.read_text())
    workload["metadata"]["namespace"] = "local/test-project"
    workload["spec"].pop("modelPackageRef")
    workload["spec"].pop("evaluationRefs")
    workload["spec"].pop("mixRef")
    workload["spec"]["graph"]["stages"] = workload["spec"]["graph"]["stages"][:1]
    workload_path.write_text(yaml.safe_dump(workload))

    module_root = paths.root / "modules/examples/affine-regression"
    if failure == "multiple":
        source = (module_root / "main.py").read_text()
        source = source.replace(
            '{"name": "checkpoint", "kind": "checkpoint", "path": model_path.name},',
            '{"name": "checkpoint", "kind": "checkpoint", "path": model_path.name},\n'
            '                {"name": "checkpoint-copy", "kind": "checkpoint", '
            '"path": model_path.name},',
            1,
        )
        (module_root / "main.py").write_text(source)
        expected = "only one aggregate checkpoint"
    elif failure == "state":
        source = (module_root / "main.py").read_text().replace("            state=model,\n", "", 1)
        (module_root / "main.py").write_text(source)
        expected = "requires protocol state"
    else:
        manifest_path = module_root / "module.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["spec"]["lifecycle"]["checkpoint"] = False
        manifest_path.write_text(yaml.safe_dump(manifest))
        expected = "without declaring support"

    with Factory(paths) as factory:
        factory.add_data(
            Path("data/fixtures/affine.jsonl").resolve(),
            name="example-affine",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        with pytest.raises(ValidationError, match=expected):
            factory.run(workload_path, paths.root / "bindings/local.yaml")
        operation = factory.operations.list()[-1]
        assert operation["state"] == "failed"
        assert (
            factory.run_status(factory.list_resources(kind="Run")[0]["metadata"]["uid"])["status"][
                "state"
            ]
            == "Failed"
        )
