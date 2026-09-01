import pytest
from omf.capacity import CapacityHarness
from omf.errors import ValidationError


def test_frontier_threshold_and_failure_accounting():
    with pytest.raises(ValidationError):
        CapacityHarness().run(
            accelerators=1023,
            operations=1,
            event=lambda: None,
            artifact=lambda: None,
            control=lambda: None,
            restore=lambda: None,
            claim_frontier=True,
        )

    def fail():
        raise RuntimeError("failure")

    report = CapacityHarness().run(
        accelerators=1,
        operations=2,
        event=fail,
        artifact=lambda: None,
        control=lambda: None,
        restore=lambda: None,
    )
    assert report.failures == 2
