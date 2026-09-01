"""Project discovery, local bootstrap, and configuration loading."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omf.canonical import load_document
from omf.database import Database
from omf.errors import ConfigurationError, NotFoundError, ValidationError
from omf.schema_registry import default_registry
from omf.security import ApiTokenStore, SecretStore, SigningIdentity
from omf.stores.filesystem import FilesystemStore


@dataclass(frozen=True)
class ProjectPaths:
    """All repository-scoped paths used by the local profile."""

    root: Path

    @property
    def config(self) -> Path:
        return self.root / "omf.yaml"

    @property
    def state(self) -> Path:
        return self.root / ".omf"

    @property
    def database(self) -> Path:
        return self.state / "metadata.db"

    @property
    def signing_key(self) -> Path:
        return self.state / "identity" / "signing.key"

    @property
    def secret_key(self) -> Path:
        return self.state / "identity" / "secrets.key"

    @property
    def store(self) -> Path:
        return self.state / "store"

    @property
    def runs(self) -> Path:
        return self.state / "runs"

    @property
    def packages(self) -> Path:
        return self.state / "packages"

    @property
    def telemetry(self) -> Path:
        return self.state / "telemetry" / "telemetry.jsonl"


def discover_project(start: str | Path | None = None) -> ProjectPaths:
    """Find the nearest parent containing ``omf.yaml`` without crossing the filesystem root."""
    current = Path(start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "omf.yaml").is_file():
            return ProjectPaths(candidate)
    raise ConfigurationError("no omf.yaml found in this directory or its parents")


def load_project(paths: ProjectPaths) -> dict[str, Any]:
    """Load and validate the project resource."""
    value = load_document(paths.config.read_bytes())
    if not isinstance(value, dict):
        raise ValidationError("omf.yaml must contain one resource object")
    return default_registry.validate(value)


def bootstrap(
    paths: ProjectPaths,
    *,
    profile: str = "local",
    plan: bool = False,
) -> dict[str, Any]:
    """Idempotently initialize a repository-scoped local factory."""
    if profile != "local":
        raise ConfigurationError(
            "the built-in bootstrap profile is local; site profiles use bindings"
        )
    project = load_project(paths)
    directories = [
        paths.state,
        paths.state / "identity",
        paths.store,
        paths.runs,
        paths.packages,
        paths.state / "telemetry",
        paths.state / "operations",
    ]
    actions = [
        {"action": "create-directory", "path": str(path.relative_to(paths.root))}
        for path in directories
        if not path.exists()
    ]
    if not paths.database.exists():
        actions.append({"action": "initialize-database", "path": ".omf/metadata.db"})
    if not paths.signing_key.exists():
        actions.append({"action": "generate-signing-identity", "path": ".omf/identity"})
    if plan:
        return {"profile": profile, "project": project["metadata"]["name"], "actions": actions}

    old_umask = os.umask(0o077)
    try:
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        database = Database(paths.database)
        identity = SigningIdentity(paths.signing_key)
        secrets_store = SecretStore(database, paths.secret_key)
        FilesystemStore(paths.store)
        try:
            local_token = secrets_store.get("local-api-token", "api-authentication").decode()
        except NotFoundError:
            local_token = secrets.token_urlsafe(32)
            secrets_store.put("local-api-token", local_token, "api-authentication")
        owners = project["spec"].get("owners", [])
        ApiTokenStore(database).register(
            local_token,
            actor=str(owners[0]) if owners else "local-user",
            scopes={"*"},
        )
        database.close()
    finally:
        os.umask(old_umask)
    return {
        "profile": profile,
        "project": project["metadata"]["name"],
        "state": str(paths.state),
        "keyId": identity.key_id,
        "actions": actions,
        "ready": True,
    }
