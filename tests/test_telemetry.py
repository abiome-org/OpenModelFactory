import pytest
from omf.telemetry import TelemetrySink, TraceContext


def test_redaction_rotation_sorted_labels_and_trace(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    sink = TelemetrySink(path, max_bytes=180)
    trace = TraceContext("a" * 32, "b" * 16)
    record = sink.log(
        "request", fields={"token": "secret"}, labels={"z": "2", "a": "1"}, trace=trace
    )
    assert record["fields"]["token"] == "[REDACTED]"
    assert list(record["labels"]) == ["a", "z"]
    sink.log("another", fields={"message": "x" * 100})
    assert path.with_suffix(".jsonl.1").exists()


def test_label_bound_is_enforced(tmp_path):
    with pytest.raises(ValueError, match="cardinality"):
        TelemetrySink(tmp_path / "x", max_labels=1).log("x", labels={"a": "1", "b": "2"})
