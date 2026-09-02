from pathlib import Path

import pytest
import yaml
from omf.errors import ValidationError
from omf.workloads import AdmittedWorkload, RunState, Stage, StateStore, project_workload


def test_cycle_retry_and_state(tmp_path):
    with pytest.raises(ValueError, match="cycle"):
        AdmittedWorkload(
            source_digest="sha256:" + "0" * 64,
            stages=[
                Stage(name="a", module="m", needs=["b"]),
                Stage(name="b", module="m", needs=["a"]),
            ],
        )
    with pytest.raises(ValueError, match="idempotent"):
        AdmittedWorkload(
            source_digest="sha256:" + "0" * 64,
            stages=[Stage(name="a", module="m", retries=1)],
        )
    spec = AdmittedWorkload(
        source_digest="sha256:" + "0" * 64, stages=[Stage(name="a", module="m")]
    )
    store = StateStore(tmp_path / "state.json")
    store.initialize(spec)
    assert store.transition(RunState.DRAFT, RunState.VALIDATED)["state"] == "Validated"


def test_canonical_workload_projection_enforces_semantics():
    path = Path("workloads/example-statistical.yaml")
    workload = yaml.safe_load(path.read_text())
    workload["spec"]["graph"]["stages"][0]["needs"] = ["evaluate"]
    with pytest.raises(ValidationError, match="semantic"):
        project_workload(workload)

    workload = yaml.safe_load(path.read_text())
    workload["spec"]["reproducibility"] = "bitwise"
    with pytest.raises(ValidationError, match="not executable"):
        project_workload(workload)

    workload = yaml.safe_load(path.read_text())
    workload["spec"]["graph"]["stages"][1]["needs"] = ["missing"]
    with pytest.raises(ValidationError, match="semantic"):
        project_workload(workload)
