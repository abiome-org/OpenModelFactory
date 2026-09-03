from omf.capacity import CapacityHarness


def test_capacity_measurement_and_failure_accounting():
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
    assert report.accelerators_tested == 1
    assert report.failures == 2
    assert report.event_throughput > 0
    assert report.artifact_throughput > 0
    assert report.control_throughput > 0
