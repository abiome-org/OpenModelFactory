"""SQLite durable control-plane storage."""
# ruff: noqa: E501

from __future__ import annotations

import builtins
import contextlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from omf.canonical import canonical_json
from omf.errors import ConflictError, NotFoundError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resources(uid TEXT NOT NULL, revision TEXT NOT NULL, kind TEXT NOT NULL,
 data BLOB NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(uid,revision));
CREATE TABLE IF NOT EXISTS statuses(uid TEXT PRIMARY KEY, data BLOB NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS aliases(name TEXT PRIMARY KEY, uid TEXT NOT NULL, revision TEXT NOT NULL,
 version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, type TEXT NOT NULL, source TEXT NOT NULL,
 subject TEXT NOT NULL, time TEXT NOT NULL, resource_uid TEXT NOT NULL, revision TEXT NOT NULL,
 run_id TEXT, sequence INTEGER NOT NULL, data BLOB NOT NULL, digest TEXT NOT NULL,
 signature TEXT NOT NULL, UNIQUE(resource_uid,sequence));
CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY REFERENCES events(id), published_at TEXT);
CREATE TABLE IF NOT EXISTS lineage(source TEXT NOT NULL, target TEXT NOT NULL, relation TEXT NOT NULL,
 run_id TEXT, data BLOB NOT NULL, PRIMARY KEY(source,target,relation));
CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
 data BLOB NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS secrets(name TEXT PRIMARY KEY, purpose TEXT NOT NULL, nonce BLOB NOT NULL,
 ciphertext BLOB NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS federation_peers(id TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS federation_inbox(peer_id TEXT NOT NULL, event_id TEXT NOT NULL,
 data BLOB NOT NULL, PRIMARY KEY(peer_id,event_id));
CREATE TABLE IF NOT EXISTS federation_outbox(peer_id TEXT NOT NULL, event_id TEXT NOT NULL,
 data BLOB NOT NULL, published_at TEXT, PRIMARY KEY(peer_id,event_id));
CREATE INDEX IF NOT EXISTS events_resource_idx ON events(resource_uid,sequence);
CREATE INDEX IF NOT EXISTS events_run_idx ON events(run_id);
CREATE INDEX IF NOT EXISTS events_type_time_idx ON events(type,time);
CREATE INDEX IF NOT EXISTS lineage_target_idx ON lineage(target);
CREATE TRIGGER IF NOT EXISTS resources_no_update BEFORE UPDATE ON resources
 BEGIN SELECT RAISE(ABORT,'immutable resource'); END;
CREATE TRIGGER IF NOT EXISTS resources_no_delete BEFORE DELETE ON resources
 BEGIN SELECT RAISE(ABORT,'immutable resource'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
 BEGIN SELECT RAISE(ABORT,'immutable event'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
 BEGIN SELECT RAISE(ABORT,'immutable event'); END;
CREATE TRIGGER IF NOT EXISTS lineage_no_update BEFORE UPDATE ON lineage
 BEGIN SELECT RAISE(ABORT,'immutable lineage'); END;
CREATE TRIGGER IF NOT EXISTS lineage_no_delete BEFORE DELETE ON lineage
 BEGIN SELECT RAISE(ABORT,'immutable lineage'); END;
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS api_tokens(token_hash TEXT PRIMARY KEY, actor TEXT NOT NULL,
 scopes BLOB NOT NULL, expires_at TEXT, created_at TEXT NOT NULL, revoked_at TEXT);
CREATE INDEX IF NOT EXISTS api_tokens_actor_idx ON api_tokens(actor,revoked_at);
"""

_SCHEMA_V3 = """
CREATE INDEX IF NOT EXISTS resources_kind_uid_created_idx
 ON resources(kind,uid,created_at,revision);
CREATE INDEX IF NOT EXISTS resources_created_idx ON resources(created_at,uid);
CREATE INDEX IF NOT EXISTS operations_state_id_idx ON operations(state,id);
"""

_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS event_order(position INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE REFERENCES events(id));
INSERT OR IGNORE INTO event_order(event_id) SELECT id FROM events ORDER BY time,id;
CREATE TRIGGER IF NOT EXISTS event_order_no_update BEFORE UPDATE ON event_order
 BEGIN SELECT RAISE(ABORT,'immutable event order'); END;
CREATE TRIGGER IF NOT EXISTS event_order_no_delete BEFORE DELETE ON event_order
 BEGIN SELECT RAISE(ABORT,'immutable event order'); END;
"""


class Database:
    """A SQLite database; connections are isolated per thread."""

    def __init__(self, path: str | Path, *, busy_timeout: int = 5000) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout = busy_timeout
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout}")
        connection.execute("PRAGMA journal_mode=WAL")
        with self._connections_lock:
            self._connections.add(connection)
        return connection

    @property
    def connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self.connect()
            self._local.connection = connection
        return connection

    @contextlib.contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()

    def migrate(self) -> None:
        connection = self.connection
        with self.transaction(immediate=True):
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)"
            )
            if (
                connection.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone()
                is None
            ):
                statement = ""
                for line in _SCHEMA.splitlines():
                    statement += line + "\n"
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                connection.execute("INSERT INTO schema_migrations VALUES(1)")
            if (
                connection.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone()
                is None
            ):
                statement = ""
                for line in _SCHEMA_V2.splitlines():
                    statement += line + "\n"
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                connection.execute("INSERT INTO schema_migrations VALUES(2)")
            if (
                connection.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone()
                is None
            ):
                statement = ""
                for line in _SCHEMA_V3.splitlines():
                    statement += line + "\n"
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                connection.execute("INSERT INTO schema_migrations VALUES(3)")
            if (
                connection.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone()
                is None
            ):
                statement = ""
                for line in _SCHEMA_V4.splitlines():
                    statement += line + "\n"
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                connection.execute("INSERT INTO schema_migrations VALUES(4)")

    def rebuild_indices(self) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute("REINDEX")

    def integrity_check(self) -> bool:
        result = self.connection.execute("PRAGMA integrity_check").fetchone()[0]
        return bool(result == "ok")

    def backup(self, destination: str | Path) -> None:
        target = sqlite3.connect(destination)
        try:
            self.connection.backup(target)
        finally:
            target.close()

    def close(self) -> None:
        with self._connections_lock:
            connections = tuple(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()
        if hasattr(self._local, "connection"):
            del self._local.connection


class ResourceRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    def put(
        self, uid: str, revision: str, kind: str, value: Any, *, created_at: str
    ) -> dict[str, Any]:
        from omf.canonical import sha256_digest

        raw, digest = canonical_json(value), sha256_digest(value)
        try:
            with self.db.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO resources VALUES(?,?,?,?,?,?)",
                    (uid, revision, kind, raw, digest, created_at),
                )
        except sqlite3.IntegrityError as exc:
            row = self.db.connection.execute(
                "SELECT data FROM resources WHERE uid=? AND revision=?", (uid, revision)
            ).fetchone()
            if row is None or bytes(row[0]) != raw:
                raise ConflictError(
                    "immutable resource identity already has different content"
                ) from exc
        return self.get(uid, revision)

    def get(self, uid: str, revision: str) -> dict[str, Any]:
        row = self.db.connection.execute(
            "SELECT data FROM resources WHERE uid=? AND revision=?", (uid, revision)
        ).fetchone()
        if row is None:
            raise NotFoundError("resource not found")
        value: dict[str, Any] = json.loads(row[0])
        return value

    def list(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT data FROM resources"
        args: tuple[Any, ...] = ()
        if kind is not None:
            query += " WHERE kind=?"
            args = (kind,)
        return [
            json.loads(row[0])
            for row in self.db.connection.execute(query + " ORDER BY uid,revision", args)
        ]

    def latest(
        self, *, kind: str | None = None, limit: int | None = None
    ) -> builtins.list[dict[str, Any]]:
        """Return the newest immutable revision of each logical resource."""
        if limit is not None and limit < 1:
            return []
        where = "WHERE kind=?" if kind is not None else ""
        args: builtins.list[Any] = [kind] if kind is not None else []
        query = f"""
            WITH ranked AS (
              SELECT data,created_at,uid,revision,
                ROW_NUMBER() OVER (
                  PARTITION BY uid ORDER BY created_at DESC,revision DESC
                ) AS position
              FROM resources {where}
            )
            SELECT data FROM ranked WHERE position=1
            ORDER BY created_at DESC,uid DESC
        """
        if limit is not None:
            query += " LIMIT ?"
            args.append(limit)
        return [json.loads(row[0]) for row in self.db.connection.execute(query, args)]

    def inventory(self) -> builtins.list[dict[str, Any]]:
        """Return bounded per-kind object/revision counts without loading resource bodies."""
        rows = self.db.connection.execute(
            """
            SELECT kind,COUNT(DISTINCT uid),COUNT(*),MAX(created_at)
            FROM resources GROUP BY kind ORDER BY kind
            """
        )
        return [
            {
                "kind": str(row[0]),
                "objects": int(row[1]),
                "revisions": int(row[2]),
                "latestAt": str(row[3]),
            }
            for row in rows
        ]

    def get_status(self, uid: str) -> tuple[dict[str, Any], int]:
        row = self.db.connection.execute(
            "SELECT data,version FROM statuses WHERE uid=?", (uid,)
        ).fetchone()
        if row is None:
            raise NotFoundError("status not found")
        return json.loads(row[0]), int(row[1])

    def set_status(self, uid: str, value: Any, *, expected_version: int | None) -> int:
        raw = canonical_json(value)
        with self.db.transaction(immediate=True) as connection:
            row = connection.execute("SELECT version FROM statuses WHERE uid=?", (uid,)).fetchone()
            current = None if row is None else int(row[0])
            if current != expected_version:
                raise ConflictError(
                    "status version mismatch",
                    details={"expectedVersion": expected_version, "currentVersion": current},
                )
            version = 1 if current is None else current + 1
            connection.execute(
                "INSERT INTO statuses VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET data=excluded.data,version=excluded.version",
                (uid, raw, version),
            )
        return version


class AliasRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    def move(self, name: str, uid: str, revision: str, *, expected_version: int | None) -> int:
        with self.db.transaction(immediate=True) as connection:
            row = connection.execute("SELECT version FROM aliases WHERE name=?", (name,)).fetchone()
            current = None if row is None else int(row[0])
            if current != expected_version:
                raise ConflictError(
                    "alias version mismatch",
                    details={"expectedVersion": expected_version, "currentVersion": current},
                )
            version = 1 if current is None else current + 1
            connection.execute(
                "INSERT INTO aliases VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET uid=excluded.uid,revision=excluded.revision,version=excluded.version",
                (name, uid, revision, version),
            )
            return version

    def get(self, name: str) -> tuple[str, str, int]:
        row = self.db.connection.execute(
            "SELECT uid,revision,version FROM aliases WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            raise NotFoundError("alias not found")
        return str(row[0]), str(row[1]), int(row[2])
