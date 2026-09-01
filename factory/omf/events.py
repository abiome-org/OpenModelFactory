"""Signed CloudEvents log with a transactional outbox."""
# ruff: noqa: A002, E501

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from omf.canonical import canonical_json, sha256_digest
from omf.database import Database
from omf.errors import IntegrityError, NotFoundError
from omf.ids import uuid7
from omf.security import SigningIdentity, verify


@dataclass(frozen=True)
class CloudEvent:
    specversion: str
    id: str
    type: str
    source: str
    subject: str
    time: str
    resource_uid: str
    revision: str
    sequence: int
    data: Any
    dataschema: str
    datacontenttype: str
    payload_digest: str
    actor: str
    key_id: str
    signature: str
    run_id: str | None = None
    workload_digest: str | None = None
    binding_digest: str | None = None
    policy_digest: str | None = None

    def unsigned(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("signature")
        return result

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CloudEvent:
        return cls(**value)


class EventStore:
    def __init__(self, database: Database, identity: SigningIdentity) -> None:
        self.db, self.identity = database, identity

    def append(
        self,
        *,
        type: str,
        source: str,
        subject: str,
        resource_uid: str,
        revision: str,
        actor: str,
        data: Any,
        dataschema: str,
        run_id: str | None = None,
        mutation: Callable[[sqlite3.Connection], None] | None = None,
        outbox: bool = True,
        **digests: str | None,
    ) -> CloudEvent:
        with self.db.transaction(immediate=True) as connection:
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE resource_uid=?",
                    (resource_uid,),
                ).fetchone()[0]
            )
            fields: dict[str, Any] = {
                "specversion": "1.0",
                "id": str(uuid7()),
                "type": type,
                "source": source,
                "subject": subject,
                "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "resource_uid": resource_uid,
                "revision": revision,
                "sequence": sequence,
                "data": data,
                "dataschema": dataschema,
                "datacontenttype": "application/json",
                "payload_digest": sha256_digest(data),
                "actor": actor,
                "key_id": self.identity.key_id,
                "run_id": run_id,
                "workload_digest": digests.get("workload_digest"),
                "binding_digest": digests.get("binding_digest"),
                "policy_digest": digests.get("policy_digest"),
            }
            event = CloudEvent(signature=self.identity.sign(fields), **fields)
            raw = canonical_json(event.as_dict())
            connection.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.id,
                    event.type,
                    event.source,
                    event.subject,
                    event.time,
                    event.resource_uid,
                    event.revision,
                    event.run_id,
                    event.sequence,
                    raw,
                    sha256_digest(event.as_dict()),
                    event.signature,
                ),
            )
            if mutation is not None:
                mutation(connection)
            if outbox:
                connection.execute("INSERT INTO outbox(event_id) VALUES(?)", (event.id,))
            return event

    def import_event(
        self, event: CloudEvent, public_key: bytes, *, outbox: bool = False
    ) -> CloudEvent:
        self._verify(event, public_key)
        raw = canonical_json(event.as_dict())
        try:
            with self.db.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event.id,
                        event.type,
                        event.source,
                        event.subject,
                        event.time,
                        event.resource_uid,
                        event.revision,
                        event.run_id,
                        event.sequence,
                        raw,
                        sha256_digest(event.as_dict()),
                        event.signature,
                    ),
                )
                if outbox:
                    connection.execute("INSERT INTO outbox(event_id) VALUES(?)", (event.id,))
        except sqlite3.IntegrityError as exc:
            row = self.db.connection.execute(
                "SELECT data FROM events WHERE id=?", (event.id,)
            ).fetchone()
            if row is None or bytes(row[0]) != raw:
                raise IntegrityError("duplicate event identity has different content") from exc
        return event

    @staticmethod
    def _verify(event: CloudEvent, public_key: bytes) -> None:
        if event.specversion != "1.0" or event.payload_digest != sha256_digest(event.data):
            raise IntegrityError("event payload integrity failure")
        verify(public_key, event.unsigned(), event.signature)

    def get(self, event_id: str, *, public_key: bytes | None = None) -> CloudEvent:
        row = self.db.connection.execute(
            "SELECT data FROM events WHERE id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("event not found")
        event = CloudEvent.from_dict(json.loads(row[0]))
        self._verify(event, public_key or self.identity.public_bytes)
        return event

    def query(
        self,
        *,
        run_id: str | None = None,
        resource_uid: str | None = None,
        type: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[CloudEvent]:
        clauses, args = [], []
        for column, value in (("run_id", run_id), ("resource_uid", resource_uid), ("type", type)):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        if since is not None:
            clauses.append("time>=?")
            args.append(since)
        if until is not None:
            clauses.append("time<=?")
            args.append(until)
        sql = (
            "SELECT data FROM events"
            + ((" WHERE " + " AND ".join(clauses)) if clauses else "")
            + " ORDER BY time,id"
        )
        return [
            CloudEvent.from_dict(json.loads(row[0]))
            for row in self.db.connection.execute(sql, args)
        ]

    def pending(self) -> list[CloudEvent]:
        rows = self.db.connection.execute(
            "SELECT e.data FROM events e JOIN outbox o ON o.event_id=e.id WHERE o.published_at IS NULL ORDER BY e.time,e.id"
        )
        return [CloudEvent.from_dict(json.loads(row[0])) for row in rows]

    def mark_published(self, event_id: str, *, at: datetime | None = None) -> None:
        timestamp = (at or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
        with self.db.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE outbox SET published_at=COALESCE(published_at,?) WHERE event_id=?",
                (timestamp, event_id),
            )
            if cursor.rowcount == 0:
                raise NotFoundError("outbox event not found")
