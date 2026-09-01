from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from omf.database import Database
from omf.errors import IntegrityError, NotFoundError
from omf.events import EventStore
from omf.security import SigningIdentity


def _store(tmp_path, name="events"):
    database = Database(tmp_path / f"{name}.db")
    identity = SigningIdentity(tmp_path / f"{name}.key")
    return database, identity, EventStore(database, identity)


def _append(store, **kwargs):
    values = {
        "type": "RunStateChanged",
        "source": "omf://test",
        "subject": "run/one",
        "resource_uid": "resource-one",
        "revision": "sha256:" + "a" * 64,
        "actor": "tester",
        "data": {"state": "Running"},
        "dataschema": "omf.dev/events/run-state/v1",
        "run_id": "run-one",
    }
    values.update(kwargs)
    return store.append(**values)


def test_event_signature_query_and_outbox(tmp_path):
    _database, identity, store = _store(tmp_path)
    event = _append(store)
    assert store.get(event.id, public_key=identity.public_bytes) == event
    assert store.query(run_id="run-one", type="RunStateChanged") == [event]
    assert store.pending() == [event]
    store.mark_published(event.id)
    store.mark_published(event.id)
    assert store.pending() == []


def test_event_and_mutation_are_atomic(tmp_path):
    database, _identity, store = _store(tmp_path)

    def fail(_connection):
        raise RuntimeError("mutation failed")

    with pytest.raises(RuntimeError, match="mutation failed"):
        _append(store, mutation=fail)
    assert database.connection.execute("select count(*) from events").fetchone()[0] == 0
    assert database.connection.execute("select count(*) from outbox").fetchone()[0] == 0


def test_concurrent_sequences_are_unique_and_monotonic(tmp_path):
    _database, _identity, store = _store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(pool.map(lambda index: _append(store, data={"index": index}), range(24)))
    assert sorted(event.sequence for event in events) == list(range(1, 25))
    assert len({event.id for event in events}) == 24


def test_import_is_idempotent_and_rejects_tamper(tmp_path):
    _source_db, source_identity, source = _store(tmp_path, "source")
    _target_db, _target_identity, target = _store(tmp_path, "target")
    event = _append(source)
    target.import_event(event, source_identity.public_bytes)
    target.import_event(event, source_identity.public_bytes)
    with pytest.raises(IntegrityError):
        target.import_event(
            replace(event, data={"state": "tampered"}), source_identity.public_bytes
        )
    with pytest.raises(NotFoundError):
        target.get("missing")
