import subprocess
from datetime import UTC, datetime

import pytest
from omf.agent import capability_catalog, initial_context
from omf.api import create_app
from omf.canonical import canonical_json
from omf.config import ProjectPaths, bootstrap
from omf.errors import ConflictError, ValidationError
from omf.factory import Factory


def _project(tmp_path):
    root = tmp_path / "agent-project"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "omf.yaml").write_text(
        """apiVersion: omf.dev/v1alpha1
kind: Project
metadata: {name: agent-test, namespace: local/agent-test}
spec: {owners: [local-user], extensions: {}}
"""
    )
    return ProjectPaths(root)


def test_prebootstrap_context_and_capabilities_are_stable_and_actionable(tmp_path):
    paths = _project(tmp_path)
    first_catalog = capability_catalog()
    second_catalog = capability_catalog()
    assert first_catalog == second_catalog
    assert len({item["action"] for item in first_catalog["actions"]}) == len(
        first_catalog["actions"]
    )
    assert all(
        {"preconditions", "effects", "risk", "costClass", "idempotency"} <= item.keys()
        for item in first_catalog["actions"]
    )

    first = initial_context(paths)
    second = initial_context(paths)
    assert not first["readiness"]["ready"]
    assert first["recommendations"][0]["action"] == "project.bootstrap"
    assert first["bootstrapPlan"]["actions"]
    assert first["viewDigest"] == second["viewDigest"]
    assert first["generatedAt"] != first["viewDigest"]


def test_capability_catalog_covers_every_versioned_http_operation(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    app = create_app(paths)
    try:
        openapi = app.openapi()
    finally:
        app.state.factory.close()
    methods = {"get", "post", "put", "patch", "delete"}
    api_operations = {
        (method.upper(), path)
        for path, operations in openapi["paths"].items()
        if path.startswith("/v1/")
        for method in operations
        if method in methods
    }
    catalog_operations = {
        (interface["method"], interface["path"])
        for action in capability_catalog()["actions"]
        if (interface := action["interfaces"].get("http")) is not None
    }
    assert api_operations <= catalog_operations


def test_agent_boundaries_fail_closed_with_actionable_validation(tmp_path):
    paths = _project(tmp_path)
    with pytest.raises(ValidationError, match="cursor"):
        initial_context(paths, since="unknown-event")
    with pytest.raises(ValidationError, match="max_bytes"):
        initial_context(paths, max_bytes=1)

    bootstrap(paths)
    with Factory(paths) as factory:
        factory.agent.create_goal(
            "child",
            objective="Exercise guarded boundaries",
            success_criteria=["invalid control requests fail closed"],
            parent_ref="goal/parent",
        )
        with pytest.raises(ValidationError, match="invalid goal state"):
            factory.agent.list_goals(state="unknown")
        with pytest.raises(ValidationError, match="invalid goal state"):
            factory.agent.set_goal_status(
                "child", state="unknown", expected_version=1, reason="invalid transition"
            )
        with pytest.raises(ValidationError, match="supersession target"):
            factory.agent.record_knowledge(
                "invalid",
                category="observation",
                claim="This must not be recorded.",
                confidence=1,
                evidence=[{"ref": "test:boundary"}],
                supersedes=["unknown-reference"],
            )
        with pytest.raises(ValidationError, match="max_bytes"):
            factory.agent.context(max_bytes=1)


def test_context_is_bounded_deterministic_incremental_and_redacted(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        for index in range(3):
            factory.agent.create_goal(
                f"goal-{index}",
                objective=f"Improve metric {index}",
                success_criteria=[f"metric-{index} >= 1"],
            )
        factory.operations.create("sensitive", {"token": "must-not-escape"})
        factory.events.append(
            type="SensitiveProbe",
            source="omf://local/agent-test",
            subject="probe",
            resource_uid="probe",
            revision="sha256:" + "a" * 64,
            actor="tester",
            data={"prompt": "must-not-escape"},
            dataschema="omf.dev/events/probe/v1",
        )
        at_one = datetime(2026, 9, 1, 12, tzinfo=UTC)
        at_two = datetime(2026, 9, 1, 13, tzinfo=UTC)
        first = factory.agent.context(limit=1, max_bytes=16_384, at=at_one)
        second = factory.agent.context(limit=1, max_bytes=16_384, at=at_two)
        assert len(canonical_json(first)) <= 16_384
        assert {item["name"] for item in first["inventory"]["executors"]} >= {"local"}
        assert first["goals"]["returned"] == 1
        assert first["goals"]["total"] == 3
        assert first["goals"]["truncated"]
        assert first["viewDigest"] == second["viewDigest"]
        assert first["generatedAt"] != second["generatedAt"]
        assert "must-not-escape" not in canonical_json(first).decode()

        cursor = first["recentEvents"]["cursor"]
        factory.agent.record_knowledge(
            "incremental",
            category="observation",
            claim="A new event exists.",
            confidence=1,
            evidence=[{"ref": "test:incremental"}],
        )
        incremental = factory.agent.context(since=cursor, at=at_two)
        assert incremental["recentEvents"]["since"] == cursor
        assert {item["type"] for item in incremental["recentEvents"]["items"]} >= {
            "KnowledgeRecorded"
        }


def test_goal_status_is_guarded_and_global_blockers_survive_focus(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        created = factory.agent.create_goal(
            "quality",
            objective="Improve quality",
            success_criteria=["score >= 0.9"],
            constraints=["Do not exceed the budget"],
            budget={"gpuHours": 2},
        )
        assert created["statusVersion"] == 1
        identical = factory.agent.create_goal(
            "quality",
            objective="Improve quality",
            success_criteria=["score >= 0.9"],
            constraints=["Do not exceed the budget"],
            budget={"gpuHours": 2},
        )
        assert identical == created
        assert len(factory.events.query(type="GoalStatusChanged")) == 1
        with pytest.raises(ConflictError, match="different immutable intent") as changed:
            factory.agent.create_goal(
                "quality",
                objective="Replace the established objective",
                success_criteria=["score >= 1.0"],
            )
        assert changed.value.details["currentRevision"] == created["goal"]["metadata"]["revision"]
        with pytest.raises(ValidationError, match="goal status reason"):
            factory.agent.set_goal_status("quality", state="blocked", expected_version=1, reason="")
        blocked = factory.agent.set_goal_status(
            "quality", state="blocked", expected_version=1, reason="dataset rights missing"
        )
        assert blocked["statusVersion"] == 2
        with pytest.raises(ConflictError) as stale:
            factory.agent.set_goal_status(
                "quality", state="active", expected_version=1, reason="stale retry"
            )
        assert stale.value.retryable
        assert stale.value.details["currentVersion"] == 2

        focused = factory.agent.context(focus="unrelated")
        assert focused["goals"]["items"] == []
        assert any(item["code"] == "goal_blocked" for item in focused["blockers"]["items"])

        for index in range(5):
            factory.agent.create_goal(
                f"verbose-{index}",
                objective="x" * 4096,
                success_criteria=["retain the global blocker under byte trimming"],
            )
        bounded = factory.agent.context(max_bytes=16_384)
        assert len(canonical_json(bounded)) <= 16_384
        assert bounded["goals"]["truncated"]
        assert bounded["blockers"]["returned"] == 1
        assert bounded["blockers"]["items"][0]["code"] == "goal_blocked"

        satisfied = factory.agent.set_goal_status(
            "quality", state="satisfied", expected_version=2, reason="criteria measured"
        )
        with pytest.raises(ValidationError, match="terminal goal state"):
            factory.agent.set_goal_status(
                "quality",
                state="active",
                expected_version=satisfied["statusVersion"],
                reason="reopen",
            )


def test_knowledge_requires_evidence_and_preserves_supersession_expiry_and_history(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    with Factory(paths) as factory:
        with pytest.raises(ValidationError, match="failed validation"):
            factory.agent.record_knowledge(
                "unsupported",
                category="hypothesis",
                claim="No evidence supports this.",
                confidence=0.1,
                evidence=[],
            )

        baseline = factory.agent.record_knowledge(
            "baseline",
            category="observation",
            claim="The baseline score is 0.4.",
            confidence=0.9,
            evidence=[{"ref": "evaluation:baseline"}],
        )
        factory.agent.record_knowledge(
            "baseline-corrected",
            category="observation",
            claim="The corrected baseline score is 0.6.",
            confidence=1,
            evidence=[{"ref": "evaluation:corrected"}],
            supersedes=["knowledge/baseline"],
        )
        factory.agent.record_knowledge(
            "temporary",
            category="constraint",
            claim="Use the temporary test store.",
            confidence=1,
            evidence=[{"ref": "policy:test"}],
            expires_at="2030-01-01T00:00:00Z",
        )

        active = factory.agent.list_knowledge()
        assert "baseline" not in {item["knowledge"]["metadata"]["name"] for item in active["items"]}
        historical = factory.agent.list_knowledge(active_only=False)
        old = next(
            item
            for item in historical["items"]
            if item["knowledge"]["metadata"]["name"] == "baseline"
        )
        assert old["inactiveReasons"] == ["superseded"]
        corrected = next(
            item
            for item in historical["items"]
            if item["knowledge"]["metadata"]["name"] == "baseline-corrected"
        )
        assert factory._resource_uri(baseline) in corrected["knowledge"]["spec"]["supersedes"]

        expired = factory.agent.list_knowledge(at=datetime(2031, 1, 1, tzinfo=UTC))
        assert "temporary" not in {
            item["knowledge"]["metadata"]["name"] for item in expired["items"]
        }

        before = len(factory.events.query(type="KnowledgeRecorded"))
        factory.agent.record_knowledge(
            "baseline-corrected",
            category="observation",
            claim="The corrected baseline score is 0.6.",
            confidence=1,
            evidence=[{"ref": "evaluation:corrected"}],
            supersedes=["knowledge/baseline"],
        )
        assert len(factory.events.query(type="KnowledgeRecorded")) == before


def test_recommendations_advance_from_data_gap_without_guessing_cost(tmp_path):
    paths = _project(tmp_path)
    bootstrap(paths)
    payload = paths.root / "samples.jsonl"
    payload.write_text('{"value":1}\n')
    with Factory(paths) as factory:
        initial = factory.agent.context()
        assert "data.add" in {item["action"] for item in initial["recommendations"]}
        factory.add_data(
            payload,
            name="samples",
            mode="copy",
            rights={"license": "CC0-1.0", "trainingAllowed": True},
        )
        advanced = factory.agent.context()
        actions = {item["action"] for item in advanced["recommendations"]}
        assert "data.add" not in actions
        assert "workload.run" in actions
        run = next(item for item in advanced["recommendations"] if item["action"] == "workload.run")
        assert run["estimatedCost"]["class"] == "compute"
        assert run["approvalRequired"]
