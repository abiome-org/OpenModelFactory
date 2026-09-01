from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from omf.database import Database
from omf.errors import ConflictError, IntegrityError
from omf.federation import CapacityOffer, FederationBroker, Lease
from omf.security import SigningIdentity


def _expiry():
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")


def test_signed_reconciliation_idempotence_sequence_and_alias_conflict(tmp_path):
    sender = FederationBroker(SigningIdentity(tmp_path / "sender.key"))
    receiver = FederationBroker(SigningIdentity(tmp_path / "receiver.key"))
    receiver.trust("sender", sender.identity.export_trust_bundle())
    receiver.issue_lease(Lease("lease", "sender", _expiry(), 1))
    first = sender.emit("sender", "lease", "alias", "candidate", {"revision": "one"})
    assert receiver.reconcile(first)
    assert not receiver.reconcile(first)
    second = sender.emit("sender", "lease", "alias", "candidate", {"revision": "two"})
    with pytest.raises(ConflictError):
        receiver.reconcile(second)


def test_federation_rejects_signature_epoch_lease_and_sequence_tamper(tmp_path):
    sender = FederationBroker(SigningIdentity(tmp_path / "sender.key"))
    receiver = FederationBroker(SigningIdentity(tmp_path / "receiver.key"))
    receiver.trust("sender", sender.identity.export_trust_bundle())
    receiver.issue_lease(Lease("lease", "sender", _expiry(), 1))
    event = sender.emit("sender", "lease", "artifact", "a", {"x": 1})
    with pytest.raises(IntegrityError):
        receiver.reconcile(replace(event, content_digest="sha256:" + "0" * 64))
    with pytest.raises(IntegrityError):
        receiver.reconcile(replace(event, policy_epoch=2))
    with pytest.raises(IntegrityError):
        receiver.reconcile(replace(event, sequence=2))


def test_residency_placement_uses_labels_and_capacity():
    offers = [
        CapacityOffer("b", frozenset({"gpu", "residency:eu"}), {"gpu": 8}, 1),
        CapacityOffer("a", frozenset({"gpu", "residency:us"}), {"gpu": 8}, 1),
    ]
    assert (
        FederationBroker.place(
            offers, required_labels={"gpu"}, residency="eu", resource="gpu", amount=4
        ).peer_id
        == "b"
    )
    with pytest.raises(IntegrityError):
        FederationBroker.place(offers, required_labels={"gpu"}, residency="apac", resource="gpu")


def test_federation_state_outbox_and_idempotence_survive_restart(tmp_path):
    sender_db = Database(tmp_path / "sender.db")
    receiver_db = Database(tmp_path / "receiver.db")
    sender_identity = SigningIdentity(tmp_path / "sender.key")
    receiver_identity = SigningIdentity(tmp_path / "receiver.key")
    sender = FederationBroker(sender_identity, database=sender_db)
    receiver = FederationBroker(receiver_identity, database=receiver_db)
    receiver.trust("sender", sender_identity.export_trust_bundle())
    receiver.issue_lease(Lease("lease", "sender", _expiry(), 1))
    event = sender.emit("sender", "lease", "artifact", "model", {"revision": "one"})
    assert sender.pending("sender") == (event,)
    sender.mark_published("sender", event.event_id)
    assert sender.pending() == ()
    assert receiver.reconcile(event)

    restarted_sender = FederationBroker(sender_identity, database=sender_db)
    restarted_receiver = FederationBroker(receiver_identity, database=receiver_db)
    assert restarted_sender.pending() == ()
    assert not restarted_receiver.reconcile(event)
    next_event = restarted_sender.emit(
        "sender", "lease", "artifact", "model-two", {"revision": "two"}
    )
    assert next_event.sequence == 2
    assert restarted_receiver.reconcile(next_event)
