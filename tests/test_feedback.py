from omf.feedback import FeedbackCollector, FeedbackSpec


def test_redaction_rejection_and_no_auto_training():
    spec = FeedbackSpec(
        "deploy",
        "release",
        frozenset({"email", "score"}),
        "quality",
        "consent",
        redacted_fields=frozenset({"email"}),
    )
    collector = FeedbackCollector(spec)
    assert collector.collect({"email": "a@example.com", "score": 1})
    assert not collector.collect({"secret": "must-not-retain"})
    dataset = collector.materialize()
    assert dataset.records[0]["email"] == "[REDACTED]"
    assert not dataset.approved_for_training
    assert "must-not-retain" not in repr(collector.rejections)
