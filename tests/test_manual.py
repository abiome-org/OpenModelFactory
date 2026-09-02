import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from omf.config import ProjectPaths
from omf.errors import IntegrityError
from omf.factory import Factory

MANUAL = Path("manual")
CHAPTERS = {
    "README.md",
    "01-operate-a-project.md",
    "02-build-a-module.md",
    "03-bring-and-partition-data.md",
    "04-design-evaluation.md",
    "05-train-and-measure-a-baseline.md",
    "06-create-a-candidate.md",
    "07-run-a-controlled-experiment.md",
    "08-add-rlvr-post-training.md",
    "09-rebind-execution.md",
    "10-release-and-deploy.md",
}
STATUS = re.compile(r"^\*\*Status: (Tested now|Conditional|Extension blueprint)\*\*$", re.M)
LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def test_manual_has_status_labels_and_valid_relative_links():
    chapters = sorted(MANUAL.glob("*.md"))
    assert {path.name for path in chapters} == CHAPTERS
    for chapter in chapters:
        content = chapter.read_text(encoding="utf-8")
        assert len(STATUS.findall(content)) == 1, chapter
        for raw_target in LINK.findall(content):
            target = raw_target.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            assert (chapter.parent / target).resolve().exists(), (chapter, raw_target)


def test_rlvr_blueprint_does_not_invent_cli_commands():
    blueprint = (MANUAL / "08-add-rlvr-post-training.md").read_text(encoding="utf-8")
    assert "**Status: Extension blueprint**" in blueprint
    assert not re.search(r"(?m)^\s*omf\b", blueprint)
    assert "independent evaluation verifier" in blueprint


def _manual_project(tmp_path: Path) -> Path:
    root = tmp_path / "manual-project"
    root.mkdir()
    shutil.copy2("omf.yaml", root / "omf.yaml")
    shutil.copy2(".gitignore", root / ".gitignore")
    (root / "bindings").mkdir()
    shutil.copy2("bindings/local.yaml", root / "bindings/local.yaml")
    (root / "workloads").mkdir()
    shutil.copy2(
        "workloads/example-from-scratch.yaml", root / "workloads/example-from-scratch.yaml"
    )
    (root / "modules/examples").mkdir(parents=True)
    shutil.copytree(
        "modules/examples/affine-regression", root / "modules/examples/affine-regression"
    )
    (root / "data/fixtures").mkdir(parents=True)
    shutil.copy2("data/fixtures/affine.jsonl", root / "data/fixtures/affine.jsonl")
    shutil.copy2("data/fixtures/rights.yaml", root / "data/fixtures/rights.yaml")
    for directory in ("model-packages", "evaluations", "mixes"):
        (root / directory).mkdir()
        shutil.copy2(f"{directory}/example-affine.yaml", root / directory / "example-affine.yaml")

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "OMF manual test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "manual-test@omf.invalid"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "Create manual fixture"], cwd=root, check=True)
    return root


def test_canonical_manual_lifecycle_executes_end_to_end(tmp_path):
    content = (MANUAL / "README.md").read_text(encoding="utf-8")
    match = re.search(
        r"<!-- manual-test: local-lifecycle -->\s*```sh\n(?P<script>.*?)\n```",
        content,
        re.S,
    )
    assert match is not None
    assert content.count("manual-test: local-lifecycle") == 1
    root = _manual_project(tmp_path)

    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    omf = binary_directory / "omf"
    omf.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m omf "$@"\n',
        encoding="utf-8",
    )
    omf.chmod(0o755)
    environment = os.environ | {
        "OMF_ACTOR": "local-user",
        "PATH": f"{binary_directory}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", "-c", match.group("script")],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "manual lifecycle completed: run/" in result.stdout

    with Factory(ProjectPaths(root)) as factory:
        assert factory.doctor()["ready"]
        assert factory.verify_data("example-affine")
        assert factory.find_resource("ArtifactStore", "secondary")["kind"] == "ArtifactStore"
        runs = factory.list_resources(kind="Run")
        assert len(runs) == 1
        run_id = runs[0]["metadata"]["uid"]
        assert factory.run_status(run_id)["status"]["state"] == "Succeeded"
        assert factory.lineage_query(f"run:{run_id}/stage:train")
        evaluations = factory.list_resources(kind="EvaluationResult")
        assert len(evaluations) == 1
        assert evaluations[0]["spec"]["scores"]["passed"]
        with pytest.raises(IntegrityError, match="promotion denied"):
            factory.create_release(
                run_id,
                name="manual-without-scanner-evidence",
                intended_use="manual test",
                promote=True,
                approvals=["independent-reviewer"],
            )


def test_source_distribution_contains_the_manual(tmp_path):
    output = tmp_path / "dist"
    result = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--no-isolation", "--outdir", str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    archive = next(output.glob("*.tar.gz"))
    with tarfile.open(archive, "r:gz") as package:
        members = {
            member.name.split("/", 1)[1] for member in package.getmembers() if "/" in member.name
        }
    expected = {path.as_posix() for path in MANUAL.glob("*.md")}
    assert expected <= members
    assert {
        "workloads/example-from-scratch.yaml",
        "modules/examples/affine-regression/module.yaml",
        "model-packages/example-affine.yaml",
        "evaluations/example-affine.yaml",
        "mixes/example-affine.yaml",
        "data/fixtures/affine.jsonl",
    } <= members
