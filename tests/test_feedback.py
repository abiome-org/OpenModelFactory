import pytest
from omf.errors import ValidationError
from omf.feedback import FeedbackCollector, FeedbackSpec, approve_and_export_for_training


def test_redaction_rejection_and_no_auto_training():
    spec = FeedbackSpec(
        "deploy",
        "release",
        frozenset({"email", "score"}),
        "quality",
        "consent",
        redacted_fields=frozenset({"email"}),
    )
    collector = FeedbackCollector(spec, collected_by="feedback-collector")
    assert collector.collect({"email": "a@example.com", "score": 1})
    assert not collector.collect({"secret": "must-not-retain"})
    dataset = collector.materialize()
    assert dataset.records[0]["email"] == "[REDACTED]"
    assert not dataset.approved_for_training
    assert "must-not-retain" not in repr(collector.rejections)

    for approver in ("", "feedback-collector"):
        with pytest.raises(ValidationError, match="different named approver"):
            approve_and_export_for_training(dataset, approver=approver)

    exported = approve_and_export_for_training(dataset, approver="training-reviewer")
    assert exported.source_revision == dataset.revision
    assert exported.approved_by == "training-reviewer"
    assert exported.records == ({"email": "[REDACTED]", "score": 1},)
    assert exported.records[0] is not dataset.records[0]
    assert not dataset.approved_for_training

    dataset.records[0]["score"] = 2
    with pytest.raises(ValidationError, match="integrity"):
        approve_and_export_for_training(dataset, approver="training-reviewer")


def test_feedback_requires_attributable_collection():
    spec = FeedbackSpec(
        "deploy",
        "release",
        frozenset({"score"}),
        "quality",
        "consent",
    )
    with pytest.raises(ValidationError, match="collector identity"):
        FeedbackCollector(spec, collected_by="")


def test_feedback_staging_and_export_do_not_share_nested_values():
    spec = FeedbackSpec(
        "deploy",
        "release",
        frozenset({"payload"}),
        "quality",
        "consent",
    )
    original = {"payload": {"labels": ["accepted"]}}
    collector = FeedbackCollector(spec, collected_by="feedback-collector")
    assert collector.collect(original)
    original["payload"]["labels"].append("poisoned")

    dataset = collector.materialize()
    exported = approve_and_export_for_training(dataset, approver="training-reviewer")
    dataset.records[0]["payload"]["labels"].append("changed-after-approval")

    assert exported.records == ({"payload": {"labels": ["accepted"]}},)
