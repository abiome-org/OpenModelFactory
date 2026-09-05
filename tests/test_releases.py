import pytest
from omf.errors import IntegrityError
from omf.releases import ReleaseBuilder, verify_release
from omf.security import SigningIdentity


def _manifest():
    return {
        "format": "omf.release/v2",
        "model": {},
        "runtime": {},
        "provenance": {},
        "dataSummary": [],
        "evaluations": [],
        "assessment": {},
    }


def test_complete_release_signing_and_tamper_detection(tmp_path):
    identity = SigningIdentity(tmp_path / "key")
    release = ReleaseBuilder(identity).build(_manifest())
    verify_release(release, identity.public_bytes)
    release.manifest["intendedUse"] = "tampered"
    with pytest.raises(IntegrityError):
        verify_release(release, identity.public_bytes)


def test_weights_only_release_is_rejected(tmp_path):
    with pytest.raises(IntegrityError, match="incomplete"):
        ReleaseBuilder(SigningIdentity(tmp_path / "key")).build({"model": {}})


def test_concurrent_promotions_commit_one_alias_move(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from omf.database import AliasRepository, Database
    from omf.errors import ConflictError
    from omf.events import EventStore
    from omf.policy import PolicyDecision
    from omf.releases import promote_alias

    database = Database(tmp_path / "metadata.db")
    events = EventStore(database, SigningIdentity(tmp_path / "key"))
    barrier = threading.Barrier(2)
    append = events.append

    def simultaneous_append(**kwargs):
        if kwargs["type"] == "AliasMoved":
            barrier.wait(timeout=5)
        return append(**kwargs)

    monkeypatch.setattr(events, "append", simultaneous_append)

    def promote(uid):
        try:
            return promote_alias(
                database,
                events,
                name="candidate",
                uid=uid,
                revision="sha256:" + "a" * 64,
                expected_version=None,
                actor="tester",
                policy_decision=PolicyDecision("allow", "sha256:policy", ()),
            )
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(promote, ["model-a", "model-b"]))
    assert sorted(results, key=str) == [1, "conflict"]
    assert AliasRepository(database).get("candidate")[2] == 1
    assert len(events.query(type="AliasMoved")) == 1
    database.close()
