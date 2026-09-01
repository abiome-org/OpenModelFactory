import subprocess
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import yaml
from fastapi.testclient import TestClient
from omf.api import create_app
from omf.config import ProjectPaths, bootstrap
from omf.factory import Factory
from omf.federation import FederationBroker
from omf.security import SigningIdentity


def _paths(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "omf.yaml").write_text(
        """apiVersion: omf.dev/v1alpha1
kind: Project
metadata:
  name: api-test
  namespace: local/api-test
spec:
  owners: [local-user]
  extensions: {}
"""
    )
    paths = ProjectPaths(root)
    bootstrap(paths)
    return paths


def test_api_health_auth_schemas_resources_and_doctor(tmp_path):
    paths = _paths(tmp_path)
    (paths.root / "bindings").mkdir()
    (paths.root / "bindings/local.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "Binding",
                "metadata": {"name": "local", "namespace": "local/api-test"},
                "spec": {"executor": "local", "resources": {}, "config": {}},
            }
        )
    )
    with Factory(paths) as factory:
        token = factory.secrets.get("local-api-token", "api-authentication").decode()
        operation_id = factory.operations.create("test", {"value": 1})["id"]
    with TestClient(create_app(paths)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/doctor").status_code == 403
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/v1/doctor", headers=headers).json()["ready"]
        providers = client.get("/v1/executors", headers=headers).json()["providers"]
        assert {item["name"] for item in providers} >= {"local", "kubernetes", "slurm"}
        preflight = client.post(
            "/v1/executors/preflight",
            headers=headers,
            json={"binding": "bindings/local.yaml"},
        )
        assert preflight.status_code == 200
        assert preflight.json()["ready"]
        outside = paths.root.parent / "outside-binding.yaml"
        outside.write_text("sensitive-marker: must-not-escape")
        escaped = client.post(
            "/v1/executors/preflight",
            headers=headers,
            json={"binding": "../outside-binding.yaml"},
        )
        assert escaped.status_code == 400
        assert "must-not-escape" not in escaped.text
        assert "Project" in client.get("/v1/schemas", headers=headers).json()["kinds"]
        resource = {
            "apiVersion": "omf.dev/v1alpha1",
            "kind": "ArtifactStore",
            "metadata": {"name": "second", "namespace": "local/api-test"},
            "spec": {"storeType": "filesystem", "location": ".omf/second"},
        }
        response = client.post("/v1/resources", headers=headers, json=resource)
        assert response.status_code == 200
        assert response.json()["kind"] == "ArtifactStore"
        first_uid = response.json()["metadata"]["uid"]
        reapplied = client.post("/v1/resources", headers=headers, json=resource)
        assert reapplied.json()["metadata"]["uid"] == first_uid
        listed = client.get("/v1/resources?kind=ArtifactStore", headers=headers).json()
        assert len(listed) == 1
        assert client.get("/v1/resources?limit=1&offset=1", headers=headers).json() == []
        operations = client.get("/v1/operations?state=pending", headers=headers).json()
        assert [item["id"] for item in operations] == [operation_id]
        assert client.get(f"/v1/operations/{operation_id}", headers=headers).json()["version"] == 1

        created = client.post(
            "/v1/tokens",
            headers=headers,
            json={"actor": "alice", "scopes": ["read", "write"]},
        ).json()
        assert created["actor"] == "alice"
        listed_tokens = client.get("/v1/tokens", headers=headers).json()
        assert created["token"] not in repr(listed_tokens)
        bootstrap_token = next(item for item in listed_tokens if item["scopes"] == ["*"])
        assert (
            client.delete(f"/v1/tokens/{bootstrap_token['tokenId']}", headers=headers).status_code
            == 400
        )
        alice_headers = {"Authorization": f"Bearer {created['token']}"}
        assert client.get("/v1/doctor", headers=alice_headers).status_code == 200
        alice_resource = {
            **resource,
            "metadata": {"name": "alice-store", "namespace": "local/api-test"},
        }
        assert (
            client.post("/v1/resources", headers=alice_headers, json=alice_resource).status_code
            == 200
        )
        alice_events = client.get(
            "/v1/events?event_type=SpecValidated", headers=alice_headers
        ).json()
        assert any(event["actor"] == "alice" for event in alice_events)

        read_only = client.post(
            "/v1/tokens",
            headers=headers,
            json={"actor": "reader", "scopes": ["read"]},
        ).json()
        read_headers = {"Authorization": f"Bearer {read_only['token']}"}
        assert client.get("/v1/doctor", headers=read_headers).status_code == 200
        assert (
            client.post(
                "/v1/executors/preflight",
                headers=read_headers,
                json={"binding": "bindings/local.yaml"},
            ).status_code
            == 200
        )
        assert client.post("/v1/resources", headers=read_headers, json=resource).status_code == 403
        assert client.delete(f"/v1/tokens/{read_only['tokenId']}", headers=headers).json()[
            "revoked"
        ]
        assert client.get("/v1/doctor", headers=read_headers).status_code == 403


def test_agent_goal_and_knowledge_api_parity_scopes_and_etags(tmp_path):
    paths = _paths(tmp_path)
    with Factory(paths) as factory:
        token = factory.secrets.get("local-api-token", "api-authentication").decode()
        reader_token, _principal = factory.api_tokens.create(actor="reader", scopes={"read"})
    headers = {"Authorization": f"Bearer {token}"}
    reader_headers = {"Authorization": f"Bearer {reader_token}"}

    with TestClient(create_app(paths)) as client:
        capabilities = client.get("/v1/agent/capabilities", headers=headers)
        assert capabilities.status_code == 200
        assert capabilities.headers["etag"]
        assert (
            client.get(
                "/v1/agent/capabilities",
                headers={**headers, "If-None-Match": capabilities.headers["etag"]},
            ).status_code
            == 304
        )

        context = client.get("/v1/agent/context?limit=2&max_bytes=16384", headers=headers)
        assert context.status_code == 200
        assert context.json()["kind"] == "AgentContext"
        assert (
            client.get(
                "/v1/agent/context?limit=2&max_bytes=16384",
                headers={**headers, "If-None-Match": context.headers["etag"]},
            ).status_code
            == 304
        )

        goal = client.post(
            "/v1/goals",
            headers=headers,
            json={
                "name": "quality",
                "objective": "Improve quality",
                "success_criteria": ["score >= 0.9"],
                "constraints": ["gpuHours <= 2"],
                "budget": {"gpuHours": 2},
            },
        )
        assert goal.status_code == 200
        assert goal.json()["statusVersion"] == 1
        assert client.get("/v1/goals?state=active", headers=reader_headers).json()["total"] == 1
        assert client.post("/v1/goals", headers=reader_headers, json={}).status_code == 403

        status = client.patch(
            "/v1/goals/quality/status",
            headers=headers,
            json={"state": "blocked", "expected_version": 1, "reason": "awaiting evidence"},
        )
        assert status.json()["statusVersion"] == 2
        stale = client.patch(
            "/v1/goals/quality/status",
            headers=headers,
            json={"state": "active", "expected_version": 1, "reason": "stale"},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["retryable"]
        assert stale.json()["error"]["details"]["currentVersion"] == 2

        knowledge = client.post(
            "/v1/knowledge",
            headers=headers,
            json={
                "name": "baseline",
                "category": "observation",
                "claim": "The baseline score is 0.4.",
                "confidence": 0.9,
                "evidence": [{"ref": "evaluation:baseline"}],
                "scope": {"goal_refs": ["goal/quality"], "tags": ["quality"]},
            },
        )
        assert knowledge.status_code == 200
        assert knowledge.json()["kind"] == "Knowledge"
        assert (
            client.get("/v1/knowledge?focus=quality", headers=reader_headers).json()["total"] == 1
        )
        invalid = client.post(
            "/v1/knowledge",
            headers=headers,
            json={"name": "invalid", "claim": "sensitive-input-must-not-echo"},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "request_validation_error"
        assert "sensitive-input-must-not-echo" not in invalid.text


def test_api_federation_outbox_reconciliation_and_capacity(tmp_path):
    paths = _paths(tmp_path)
    with Factory(paths) as factory:
        token = factory.secrets.get("local-api-token", "api-authentication").decode()
    headers = {"Authorization": f"Bearer {token}"}
    sender = FederationBroker(SigningIdentity(tmp_path / "sender.key"))
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    with TestClient(create_app(paths)) as client:
        assert "keyId" in client.get("/v1/federation/identity", headers=headers).json()
        trust = client.post(
            "/v1/federation/trust",
            headers=headers,
            json={"peer_id": "sender", "trust_bundle": sender.identity.export_trust_bundle()},
        )
        assert trust.json() == {"peerId": "sender", "trusted": True}
        lease = client.post(
            "/v1/federation/leases",
            headers=headers,
            json={
                "lease_id": "lease",
                "peer_id": "sender",
                "expires_at": expires,
                "policy_epoch": 1,
            },
        )
        assert lease.status_code == 200

        remote_event = sender.emit("sender", "lease", "artifact", "model", {"revision": 1})
        reconciled = client.post(
            "/v1/federation/reconcile", headers=headers, json=asdict(remote_event)
        )
        assert reconciled.json()["accepted"]
        duplicate = client.post(
            "/v1/federation/reconcile", headers=headers, json=asdict(remote_event)
        )
        assert not duplicate.json()["accepted"]

        emitted = client.post(
            "/v1/federation/events",
            headers=headers,
            json={
                "peer_id": "receiver",
                "lease_id": "remote-lease",
                "kind": "artifact",
                "resource": "candidate",
                "content": {"digest": "sha256:value"},
            },
        ).json()
        outbox = client.get("/v1/federation/outbox?peer_id=receiver", headers=headers).json()
        assert [item["event_id"] for item in outbox] == [emitted["event_id"]]
        published = client.post(
            "/v1/federation/outbox/published",
            headers=headers,
            json={"peer_id": "receiver", "event_id": emitted["event_id"]},
        )
        assert published.json()["published"]
        assert client.get("/v1/federation/outbox", headers=headers).json() == []

        placement = client.post(
            "/v1/capacity/place",
            headers=headers,
            json={
                "offers": [
                    {
                        "peer_id": "eu-cell",
                        "labels": ["gpu", "residency:eu"],
                        "capacity": {"gpu": 8},
                        "policy_epoch": 1,
                    }
                ],
                "required_labels": ["gpu"],
                "residency": "eu",
                "resource": "gpu",
                "amount": 4,
            },
        )
        assert placement.status_code == 200
        assert placement.json()["peer_id"] == "eu-cell"
