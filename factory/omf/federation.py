"""Signed, lease-bound federation reconciliation and label-only placement."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from omf.canonical import canonical_json, sha256_digest
from omf.database import Database
from omf.errors import ConflictError, IntegrityError
from omf.security import SigningIdentity, import_trust_bundle, verify


@dataclass(frozen=True)
class CapacityOffer:
    peer_id: str
    labels: frozenset[str]
    capacity: dict[str, int]
    policy_epoch: int


@dataclass(frozen=True)
class Lease:
    lease_id: str
    peer_id: str
    expires_at: str
    policy_epoch: int


@dataclass(frozen=True)
class FederatedEvent:
    peer_id: str
    event_id: str
    sequence: int
    policy_epoch: int
    lease_id: str
    kind: str
    resource: str
    content_digest: str
    key_id: str
    signature: str

    def unsigned(self) -> dict[str, Any]:
        value = vars(self).copy()
        value.pop("signature")
        return value


class FederationBroker:
    def __init__(
        self,
        identity: SigningIdentity,
        *,
        policy_epoch: int = 1,
        database: Database | None = None,
    ) -> None:
        self.identity, self.policy_epoch = identity, policy_epoch
        self.db = database
        self.peers: dict[str, tuple[str, bytes]] = {}
        self.leases: dict[str, Lease] = {}
        self.outbox: list[FederatedEvent] = []
        self.inbox: dict[str, FederatedEvent] = {}
        self.sequences: dict[str, int] = {}
        self.aliases: dict[str, str] = {}
        if database is not None:
            self._load()

    def _load(self) -> None:
        assert self.db is not None
        for row in self.db.connection.execute("SELECT id,data FROM federation_peers"):
            identifier, value = str(row[0]), json.loads(row[1])
            if identifier.startswith("peer:"):
                self.peers[identifier.removeprefix("peer:")] = import_trust_bundle(value["bundle"])
            elif identifier.startswith("lease:"):
                lease = Lease(**value)
                self.leases[lease.lease_id] = lease
            elif identifier.startswith("sequence:"):
                self.sequences[identifier.removeprefix("sequence:")] = int(value["value"])
            elif identifier.startswith("alias:"):
                self.aliases[identifier.removeprefix("alias:")] = str(value["digest"])
        self.outbox = [
            FederatedEvent(**json.loads(row[0]))
            for row in self.db.connection.execute(
                "SELECT data FROM federation_outbox ORDER BY peer_id,event_id"
            )
        ]
        self.inbox = {
            event.event_id: event
            for event in (
                FederatedEvent(**json.loads(row[0]))
                for row in self.db.connection.execute("SELECT data FROM federation_inbox")
            )
        }

    def _put_state(self, identifier: str, value: Any, connection: Any = None) -> None:
        if self.db is None:
            return
        target = connection or self.db.connection
        target.execute(
            "INSERT INTO federation_peers(id,data) VALUES(?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (identifier, canonical_json(value)),
        )

    def trust(self, peer_id: str, bundle: dict[str, str]) -> None:
        self.peers[peer_id] = import_trust_bundle(bundle)
        if self.db is not None:
            with self.db.transaction(immediate=True) as connection:
                self._put_state(f"peer:{peer_id}", {"bundle": bundle}, connection)

    def issue_lease(self, lease: Lease) -> None:
        self.leases[lease.lease_id] = lease
        if self.db is not None:
            with self.db.transaction(immediate=True) as connection:
                self._put_state(f"lease:{lease.lease_id}", asdict(lease), connection)

    def emit(
        self, peer_id: str, lease_id: str, kind: str, resource: str, content: Any
    ) -> FederatedEvent:
        if self.db is None:
            sequence = self.sequences.get(self.identity.key_id, 0) + 1
            event = self._signed_event(peer_id, lease_id, kind, resource, content, sequence)
        else:
            with self.db.transaction(immediate=True) as connection:
                row = connection.execute(
                    "SELECT data FROM federation_peers WHERE id=?",
                    (f"sequence:{self.identity.key_id}",),
                ).fetchone()
                previous = 0 if row is None else int(json.loads(row[0])["value"])
                sequence = previous + 1
                event = self._signed_event(peer_id, lease_id, kind, resource, content, sequence)
                self._put_state(f"sequence:{self.identity.key_id}", {"value": sequence}, connection)
                connection.execute(
                    "INSERT INTO federation_outbox(peer_id,event_id,data) VALUES(?,?,?)",
                    (peer_id, event.event_id, canonical_json(asdict(event))),
                )
        self.sequences[self.identity.key_id] = sequence
        self.outbox.append(event)
        return event

    def _signed_event(
        self,
        peer_id: str,
        lease_id: str,
        kind: str,
        resource: str,
        content: Any,
        sequence: int,
    ) -> FederatedEvent:
        fields: dict[str, Any] = {
            "peer_id": peer_id,
            "event_id": sha256_digest({"key": self.identity.key_id, "sequence": sequence}),
            "sequence": sequence,
            "policy_epoch": self.policy_epoch,
            "lease_id": lease_id,
            "kind": kind,
            "resource": resource,
            "content_digest": sha256_digest(content),
            "key_id": self.identity.key_id,
        }
        event = FederatedEvent(
            peer_id=peer_id,
            event_id=str(fields["event_id"]),
            sequence=sequence,
            policy_epoch=self.policy_epoch,
            lease_id=lease_id,
            kind=kind,
            resource=resource,
            content_digest=str(fields["content_digest"]),
            key_id=self.identity.key_id,
            signature=self.identity.sign(fields),
        )
        return event

    def reconcile(self, event: FederatedEvent, *, now: datetime | None = None) -> bool:
        existing = self.inbox.get(event.event_id)
        if existing is not None:
            if existing != event:
                raise IntegrityError("duplicate immutable event differs")
            return False
        trusted = self.peers.get(event.peer_id)
        if trusted is None or trusted[0] != event.key_id:
            raise IntegrityError("untrusted peer key")
        verify(trusted[1], event.unsigned(), event.signature)
        lease = self.leases.get(event.lease_id)
        current = now or datetime.now(UTC)
        if (
            lease is None
            or lease.peer_id != event.peer_id
            or datetime.fromisoformat(lease.expires_at.replace("Z", "+00:00")) <= current
        ):
            raise IntegrityError("invalid or expired federation lease")
        if event.policy_epoch != self.policy_epoch or lease.policy_epoch != self.policy_epoch:
            raise IntegrityError("stale federation policy epoch")
        if self.db is not None:
            with self.db.transaction(immediate=True) as connection:
                duplicate = connection.execute(
                    "SELECT data FROM federation_inbox WHERE peer_id=? AND event_id=?",
                    (event.peer_id, event.event_id),
                ).fetchone()
                if duplicate is not None:
                    if FederatedEvent(**json.loads(duplicate[0])) != event:
                        raise IntegrityError("duplicate immutable event differs")
                    return False
                row = connection.execute(
                    "SELECT data FROM federation_peers WHERE id=?",
                    (f"sequence:{event.peer_id}",),
                ).fetchone()
                previous = 0 if row is None else int(json.loads(row[0])["value"])
                alias_row = connection.execute(
                    "SELECT data FROM federation_peers WHERE id=?",
                    (f"alias:{event.resource}",),
                ).fetchone()
                existing_alias = (
                    None if alias_row is None else str(json.loads(alias_row[0])["digest"])
                )
                self._accept(event, previous, existing_alias=existing_alias)
                self._put_state(f"sequence:{event.peer_id}", {"value": event.sequence}, connection)
                if event.kind == "alias":
                    self._put_state(
                        f"alias:{event.resource}", {"digest": event.content_digest}, connection
                    )
                connection.execute(
                    "INSERT INTO federation_inbox(peer_id,event_id,data) VALUES(?,?,?)",
                    (event.peer_id, event.event_id, canonical_json(asdict(event))),
                )
        else:
            self._accept(event, self.sequences.get(event.peer_id, 0))
        self.sequences[event.peer_id] = event.sequence
        if event.kind == "alias":
            self.aliases[event.resource] = event.content_digest
        self.inbox[event.event_id] = event
        return True

    def _accept(
        self,
        event: FederatedEvent,
        previous_sequence: int,
        *,
        existing_alias: str | None = None,
    ) -> None:
        if event.sequence != previous_sequence + 1:
            raise IntegrityError("federation sequence replay or gap")
        if event.kind == "alias":
            existing = existing_alias or self.aliases.get(event.resource)
            if existing is not None and existing != event.content_digest:
                raise ConflictError("federated alias conflict requires resolution")

    def pending(self, peer_id: str | None = None) -> tuple[FederatedEvent, ...]:
        if self.db is None:
            return tuple(event for event in self.outbox if peer_id in {None, event.peer_id})
        query = "SELECT data FROM federation_outbox WHERE published_at IS NULL"
        args: tuple[str, ...] = ()
        if peer_id is not None:
            query += " AND peer_id=?"
            args = (peer_id,)
        query += " ORDER BY peer_id,event_id"
        return tuple(
            FederatedEvent(**json.loads(row[0])) for row in self.db.connection.execute(query, args)
        )

    def mark_published(self, peer_id: str, event_id: str) -> None:
        if self.db is None:
            return
        with self.db.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE federation_outbox SET published_at=COALESCE(published_at,?) "
                "WHERE peer_id=? AND event_id=?",
                (datetime.now(UTC).isoformat(), peer_id, event_id),
            )
            if cursor.rowcount != 1:
                raise IntegrityError("federation outbox event not found")

    @staticmethod
    def place(
        offers: list[CapacityOffer],
        *,
        required_labels: set[str],
        residency: str,
        resource: str,
        amount: int = 1,
    ) -> CapacityOffer:
        label = f"residency:{residency}"
        eligible = [
            offer
            for offer in offers
            if required_labels <= offer.labels
            and label in offer.labels
            and offer.capacity.get(resource, 0) >= amount
        ]
        if not eligible:
            raise IntegrityError("no residency-compatible capacity offer")
        return sorted(eligible, key=lambda offer: offer.peer_id)[0]
