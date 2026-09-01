import subprocess
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

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
    with Factory(paths) as factory:
        token = factory.secrets.get("local-api-token", "api-authentication").decode()
        operation_id = factory.operations.create("test", {"value": 1})["id"]
    with TestClient(create_app(paths)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/doctor").status_code == 403
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/v1/doctor", headers=headers).json()["ready"]
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
        assert client.post("/v1/resources", headers=read_headers, json=resource).status_code == 403
        assert client.delete(f"/v1/tokens/{read_only['tokenId']}", headers=headers).json()[
            "revoked"
        ]
        assert client.get("/v1/doctor", headers=read_headers).status_code == 403


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
