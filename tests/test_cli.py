import json
import shutil
import subprocess
from pathlib import Path

import yaml
from omf.cli import app
from typer.testing import CliRunner


def test_cli_help_version_and_bootstrap_plan(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["--version"]).stdout.strip() == "0.1.0"
    assert runner.invoke(app, ["--help"]).exit_code == 0
    root = tmp_path / "project"
    root.mkdir()
    (root / "omf.yaml").write_text(
        """apiVersion: omf.dev/v1alpha1
kind: Project
metadata: {name: cli-test, namespace: local/cli-test}
spec: {owners: [local-user], extensions: {}}
"""
    )
    result = runner.invoke(
        app,
        ["--project", str(root), "--output", "json", "bootstrap", "--plan"],
    )
    assert result.exit_code == 0, result.output
    assert "initialize-database" in result.stdout
    capabilities = runner.invoke(
        app, ["--project", str(root), "--output", "json", "agent", "capabilities"]
    )
    assert capabilities.exit_code == 0
    assert json.loads(capabilities.stdout)["catalogVersion"] == 1
    context = runner.invoke(app, ["--project", str(root), "--output", "json", "agent", "context"])
    assert context.exit_code == 0
    assert json.loads(context.stdout)["recommendations"][0]["action"] == "project.bootstrap"


def _full_project(tmp_path):
    root = tmp_path / "full-project"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "omf.yaml").write_text(
        """apiVersion: omf.dev/v1alpha1
kind: Project
metadata: {name: cli-full, namespace: local/cli-full}
spec: {owners: [local-user], extensions: {}}
"""
    )
    shutil.copytree("modules", root / "modules")
    shutil.copytree("workloads", root / "workloads")
    workload = yaml.safe_load((root / "workloads/example-statistical.yaml").read_text())
    workload["metadata"]["namespace"] = "local/cli-full"
    (root / "workloads/example-statistical.yaml").write_text(yaml.safe_dump(workload))
    (root / "bindings").mkdir()
    binding = yaml.safe_load(Path("bindings/local.yaml").read_text())
    binding["metadata"]["namespace"] = "local/cli-full"
    (root / "bindings/local.yaml").write_text(yaml.safe_dump(binding))
    (root / "numbers.jsonl").write_text(Path("data/fixtures/numbers.jsonl").read_text())
    (root / "rights.yaml").write_text("license: CC0-1.0\ntrainingAllowed: true\n")
    return root


def test_cli_complete_local_lifecycle(tmp_path):
    root = _full_project(tmp_path)
    runner = CliRunner()

    def invoke(*arguments):
        result = runner.invoke(
            app, ["--project", str(root), "--output", "json", *map(str, arguments)]
        )
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    assert invoke("bootstrap")["ready"]
    assert invoke("doctor")["ready"]
    goal = invoke(
        "goal",
        "create",
        "quality",
        "--objective",
        "Improve quality",
        "--success",
        "score >= 0.9",
        "--budget",
        "gpuHours=2",
    )
    assert goal["statusVersion"] == 1
    assert invoke("goal", "list", "--state", "active")["total"] == 1
    assert (
        invoke(
            "knowledge",
            "record",
            "baseline",
            "--category",
            "observation",
            "--claim",
            "The baseline score is 0.4.",
            "--confidence",
            "0.9",
            "--evidence",
            "evaluation:baseline",
            "--goal-ref",
            "goal/quality",
        )["kind"]
        == "Knowledge"
    )
    agent_context = invoke("agent", "context", "--limit", "2", "--max-bytes", "16384")
    assert agent_context["goals"]["items"][0]["goal"]["metadata"]["name"] == "quality"
    assert agent_context["knowledge"]["items"][0]["knowledge"]["metadata"]["name"] == "baseline"
    assert "Project" in invoke("schema", "list")["kinds"]
    assert invoke("schema", "show", "Project")["x-omf-kind"] == "Project"
    assert invoke("schema", "validate", root / "omf.yaml")["kind"] == "Project"
    assert (
        invoke(
            "store",
            "add",
            "secondary",
            "--driver",
            "filesystem",
            "--endpoint",
            ".omf/secondary",
        )["kind"]
        == "ArtifactStore"
    )
    assert len(invoke("store", "list")) == 1
    assert (
        invoke(
            "data",
            "add",
            root / "numbers.jsonl",
            "--name",
            "example-numbers",
            "--mode",
            "copy",
            "--rights",
            root / "rights.yaml",
        )["kind"]
        == "DatasetSnapshot"
    )
    assert invoke("data", "verify", "example-numbers")["valid"]
    assert len(invoke("data", "list")) == 1
    manifest = root / "modules/examples/statistical/module.yaml"
    validated_module = invoke("module", "validate", manifest)[0]
    assert validated_module["valid"]
    assert invoke("module", "test", manifest)[0]["passed"] == 1
    assert {item["name"] for item in invoke("executor", "list")["providers"]} >= {"local"}
    assert invoke(
        "executor",
        "preflight",
        root / "bindings/local.yaml",
        "--workload",
        root / "workloads/example-statistical.yaml",
    )["ready"]
    plan = invoke("sync", "push", "dataset/example-numbers", "--to", "secondary", "--plan")
    assert not plan["mutates"]
    assert invoke("sync", "push", "dataset/example-numbers", "--to", "secondary")["committed"]
    run = invoke(
        "run",
        root / "workloads/example-statistical.yaml",
        "--binding",
        root / "bindings/local.yaml",
    )
    assert run["state"] == "Succeeded"
    run_id = run["runId"]
    run_status = invoke("runs", "status", run_id)
    assert run_status["status"]["state"] == "Succeeded"
    evaluation = invoke("evaluate", f"run/{run_id}")
    assert evaluation["spec"]["scores"]["passed"]
    evaluation_ref = (
        f"omf://local/cli-full/evaluationresult/{evaluation['metadata']['name']}"
        f"@{evaluation['metadata']['revision']}"
    )
    invalid_experiment = runner.invoke(
        app,
        [
            "--project",
            str(root),
            "--output",
            "json",
            "experiment",
            "create",
            "self-comparison",
            "--baseline",
            evaluation_ref,
            "--candidate",
            evaluation_ref,
            "--metric",
            "passed",
            "--direction",
            "sideways",
        ],
    )
    assert invalid_experiment.exit_code == 1
    assert json.loads(invalid_experiment.stdout)["error"]["code"] == "validation_error"
    vulnerability_report = root / "vulnerability-report.yaml"
    vulnerability_report.write_text(
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
    release = invoke(
        "release",
        "create",
        run_id,
        "--name",
        "release-one",
        "--intended-use",
        "test",
        "--promote",
        "--approval",
        "reviewer",
        "--vulnerability-report",
        vulnerability_report,
    )
    assert release["kind"] == "Release"
    assert invoke("lineage", "show", f"run:{run_id}/stage:train")
    assert invoke("resource", "list", "--kind", "Release")[0]["metadata"]["name"] == "release-one"
    deployment = {
        "apiVersion": "omf.dev/v1alpha1",
        "kind": "DeploymentSpec",
        "metadata": {"name": "edge-one", "namespace": "local/cli-full"},
        "spec": {
            "releaseRef": "release/release-one",
            "runtime": "omf.module/v1",
            "routing": {},
            "extensions": {"form": "edge"},
        },
    }
    deployment_path = root / "deployment.yaml"
    deployment_path.write_text(yaml.safe_dump(deployment))
    assert invoke("deploy", deployment_path)["state"] == "packaged"
    assert invoke("deployment", "status", "edge-one")["status"]["state"] == "packaged"

    assert "keyId" in invoke("federation", "identity")
    content = root / "federated-content.yaml"
    content.write_text("revision: one\n")
    event = invoke(
        "federation",
        "emit",
        "receiver",
        "--content",
        content,
        "--lease-id",
        "lease",
        "--kind",
        "artifact",
        "--resource",
        "candidate",
    )
    assert (
        invoke("federation", "outbox", "--peer-id", "receiver")[0]["event_id"] == event["event_id"]
    )
    assert invoke("federation", "published", "receiver", event["event_id"])["published"]

    offers = root / "offers.yaml"
    offers.write_text("- peer_id: eu-cell\n  labels: [gpu, 'residency:eu']\n  capacity: {gpu: 8}\n")
    placed = invoke(
        "capacity",
        "place",
        offers,
        "--residency",
        "eu",
        "--resource",
        "gpu",
        "--required-label",
        "gpu",
    )
    assert placed["peer_id"] == "eu-cell"
    operations = invoke("operation", "list")
    assert len(operations) == 1
    assert operations[0]["kind"] == "run"
    assert operations[0]["state"] == "succeeded"
    api_token = invoke("token", "create", "--actor", "reader", "--scope", "read")
    assert api_token["actor"] == "reader"
    assert api_token["token"] not in repr(invoke("token", "list"))
    assert invoke("token", "revoke", api_token["tokenId"])["revoked"]
    assert (
        invoke("secret", "set", "example", "--purpose", "test", "--value", "not-printed")["version"]
        == 1
    )
    assert (
        invoke(
            "secret",
            "set",
            "example",
            "--purpose",
            "test",
            "--value",
            "replacement",
            "--expected-version",
            "1",
        )["version"]
        == 2
    )
    assert "example" in {item["name"] for item in invoke("secret", "list")}
    backup = invoke("backup", root.parent / "factory.omf-backup")
    assert backup["integrity"]
    restored = root.parent / "restored"
    restored.mkdir()
    shutil.copy(root / "omf.yaml", restored / "omf.yaml")
    restoration = runner.invoke(
        app,
        [
            "--project",
            str(restored),
            "--output",
            "json",
            "restore",
            backup["path"],
            "--expected-key-id",
            backup["keyId"],
        ],
    )
    assert restoration.exit_code == 0, restoration.output
    assert json.loads(restoration.stdout)["keyId"] == backup["keyId"]
