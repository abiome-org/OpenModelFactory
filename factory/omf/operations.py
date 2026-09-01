"""Durable operation records used by CLI detach/reattach and APIs."""

from __future__ import annotations

import builtins
import json
from datetime import UTC, datetime
from typing import Any

from omf.canonical import canonical_json
from omf.database import Database
from omf.errors import ConflictError, NotFoundError
from omf.ids import uuid7


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class OperationStore:
    def __init__(self, database: Database) -> None:
        self.db = database

    def create(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        operation = {
            "id": str(uuid7()),
            "kind": kind,
            "state": "pending",
            "request": request,
            "result": None,
            "error": None,
            "createdAt": _now(),
            "updatedAt": _now(),
        }
        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO operations(id,kind,state,data,version) VALUES(?,?,?,?,?)",
                (
                    operation["id"],
                    kind,
                    operation["state"],
                    canonical_json(operation),
                    1,
                ),
            )
        return operation

    def get(self, operation_id: str) -> dict[str, Any]:
        row = self.db.connection.execute(
            "SELECT data,version FROM operations WHERE id=?", (operation_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("operation not found")
        value: dict[str, Any] = json.loads(row[0])
        value["version"] = int(row[1])
        return value

    def update(
        self,
        operation_id: str,
        *,
        expected_version: int,
        state: str,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT data,version FROM operations WHERE id=?", (operation_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("operation not found")
            if int(row[1]) != expected_version:
                raise ConflictError("operation version mismatch")
            value = json.loads(row[0])
            value.update({"state": state, "result": result, "error": error, "updatedAt": _now()})
            version = expected_version + 1
            cursor = connection.execute(
                "UPDATE operations SET state=?,data=?,version=? WHERE id=? AND version=?",
                (state, canonical_json(value), version, operation_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise ConflictError("operation compare-and-set failed")
        return self.get(operation_id)

    def list(self, *, state: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id FROM operations"
        args: tuple[str, ...] = ()
        if state is not None:
            query += " WHERE state=?"
            args = (state,)
        query += " ORDER BY id"
        return [self.get(str(row[0])) for row in self.db.connection.execute(query, args)]

    def recent(
        self, *, states: set[str] | None = None, limit: int = 20
    ) -> builtins.list[dict[str, Any]]:
        """Return a bounded newest-first operation window."""
        if limit < 1:
            return []
        query = "SELECT id FROM operations"
        args: builtins.list[Any] = []
        if states:
            placeholders = ",".join("?" for _ in states)
            query += f" WHERE state IN ({placeholders})"
            args.extend(sorted(states))
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [self.get(str(row[0])) for row in self.db.connection.execute(query, args)]
