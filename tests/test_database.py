import sqlite3

import pytest
from omf.database import AliasRepository, Database, ResourceRepository
from omf.errors import ConflictError, NotFoundError


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "state.db")
    yield database
    database.close()


def test_migration_idempotence_and_integrity(db):
    db.migrate()
    assert db.integrity_check()
    assert db.connection.execute("select count(*) from schema_migrations").fetchone()[0] == 2


def test_resource_immutable_and_idempotent(db):
    repo = ResourceRepository(db)
    assert repo.put("u", "r", "K", {"x": 1}, created_at="now") == {"x": 1}
    repo.put("u", "r", "K", {"x": 1}, created_at="now")
    with pytest.raises(ConflictError):
        repo.put("u", "r", "K", {"x": 2}, created_at="now")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.connection.execute("update resources set kind='Z'")


def test_status_compare_and_swap(db):
    repo = ResourceRepository(db)
    assert repo.set_status("u", {"phase": "a"}, expected_version=None) == 1
    with pytest.raises(ConflictError):
        repo.set_status("u", {}, expected_version=None)
    assert repo.set_status("u", {"phase": "b"}, expected_version=1) == 2


def test_alias_compare_and_swap_and_missing(db):
    repo = AliasRepository(db)
    assert repo.move("prod", "u", "r", expected_version=None) == 1
    with pytest.raises(ConflictError):
        repo.move("prod", "u", "r2", expected_version=None)
    assert repo.get("prod") == ("u", "r", 1)
    with pytest.raises(NotFoundError):
        repo.get("none")


def test_backup_is_valid_and_contains_data(db, tmp_path):
    ResourceRepository(db).put("u", "r", "K", {"x": 1}, created_at="now")
    target = tmp_path / "backup.db"
    db.backup(target)
    copied = Database(target)
    assert ResourceRepository(copied).get("u", "r") == {"x": 1}
    assert copied.integrity_check()
    copied.close()
