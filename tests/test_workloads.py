import pytest
from omf.workloads import RunState, Stage, StateStore, WorkloadSpec


def test_cycle_retry_and_state(tmp_path):
    with pytest.raises(ValueError, match="cycle"):
        WorkloadSpec(
            stages=[
                Stage(name="a", module="m", needs=["b"]),
                Stage(name="b", module="m", needs=["a"]),
            ]
        )
    with pytest.raises(ValueError, match="idempotent"):
        WorkloadSpec(stages=[Stage(name="a", module="m", retries=1)])
    spec = WorkloadSpec(stages=[Stage(name="a", module="m")])
    store = StateStore(tmp_path / "state.json")
    store.initialize(spec)
    assert store.transition(RunState.DRAFT, RunState.VALIDATED)["state"] == "Validated"
