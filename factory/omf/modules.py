"""Module manifests, safe resolution, and reproducible source packages."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator

from omf.canonical import load_document, portable_relative_path
from omf.errors import ValidationError
from omf.executors.base import DependencyLock
from omf.schema_registry import default_registry


class ModuleManifest(BaseModel):
    """Validated runtime projection of the canonical Module resource."""

    model_config = ConfigDict(extra="forbid")
    name: str
    namespace: str
    contract_version: str
    kind: str
    code_root: str = "."
    argv: list[str]
    schemas: dict[str, Any] = Field(default_factory=dict)
    dependency_lock: str
    dependency_digest: str
    dependency_contents: bytes = Field(repr=False)
    capabilities: set[str] = Field(default_factory=set)
    platforms: set[str] = Field(default_factory=set)
    resources: dict[str, Any] = Field(default_factory=dict)
    determinism: str = "declared"
    checkpoint: bool = False
    side_effects: list[str] = Field(default_factory=list)
    concurrency: int = 1
    secrets: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)
    provenance: dict[str, str]
    fixtures: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("argv")
    @classmethod
    def argv_valid(cls, value: list[str]) -> list[str]:
        if not value or any(not item or "\x00" in item for item in value):
            raise ValueError("argv must contain non-empty arguments")
        return value


def load_manifest(path: str | Path, project_root: str | Path) -> tuple[ModuleManifest, Path]:
    manifest_path, root = Path(path).resolve(), Path(project_root).resolve()
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise ValidationError("manifest is outside project root") from exc
    raw = load_document(manifest_path.read_bytes())
    resource = default_registry.validate_as(raw, "Module")
    spec = resource["spec"]
    entry_point = spec["entryPoint"]
    environment = spec["environment"]
    lifecycle = spec["lifecycle"]
    access = spec["access"]
    manifest = ModuleManifest.model_validate(
        {
            "name": resource["metadata"]["name"],
            "namespace": resource["metadata"]["namespace"],
            "contract_version": spec["contractVersion"],
            "kind": spec["moduleKind"],
            "code_root": entry_point.get("codeRoot", "."),
            "argv": entry_point["command"],
            "schemas": spec["contracts"],
            "dependency_lock": environment.get("dependencyLock"),
            "dependency_digest": environment.get("dependencyDigest"),
            "dependency_contents": b"",
            "capabilities": spec.get("capabilities", []),
            "platforms": spec.get("platforms", []),
            "resources": spec.get("resources", {}),
            "determinism": spec.get("determinism", "declared"),
            "checkpoint": lifecycle["checkpoint"],
            "side_effects": lifecycle["sideEffects"],
            "concurrency": lifecycle["concurrency"],
            "secrets": access["secrets"],
            "network": access["network"],
            "provenance": spec["provenance"],
            "fixtures": spec.get("fixtures", []),
        }
    )
    portable_relative_path(manifest.code_root, "module code root")
    lexical = manifest_path.parent / manifest.code_root
    if lexical.is_symlink():
        raise ValidationError("module code root may not be a symlink")
    code = lexical.resolve()
    try:
        code.relative_to(root)
    except ValueError as exc:
        raise ValidationError("module code root escapes project root") from exc
    if not code.is_dir():
        raise ValidationError("module code root must be a directory")
    portable_relative_path(manifest.dependency_lock, "module dependency lock")
    lock_path = (code / manifest.dependency_lock).resolve()
    try:
        lock_path.relative_to(code)
    except ValueError as exc:
        raise ValidationError("module dependency lock escapes code root") from exc
    if not lock_path.is_file() or lock_path.is_symlink():
        raise ValidationError("module dependency lock must be a regular file")
    lock_contents = lock_path.read_bytes()
    actual_digest = "sha256:" + hashlib.sha256(lock_contents).hexdigest()
    if actual_digest != manifest.dependency_digest:
        raise ValidationError("module dependency lock digest does not match")
    manifest = manifest.model_copy(update={"dependency_contents": lock_contents})
    for name, contract in manifest.schemas.items():
        validate_contract_schema(contract, f"module {name}")
    if manifest.resources:
        raise ValidationError(
            "module resource requirements are not executable; declare placement in Binding"
        )
    if manifest.network:
        raise ValidationError(
            "module network destinations require an executor with allowlist enforcement"
        )
    if manifest.capabilities or manifest.platforms or manifest.secrets:
        raise ValidationError(
            "module capability, platform, and secret requirements need provider negotiation"
        )
    executable = manifest.argv[0]
    if executable.startswith("/"):
        raise ValidationError("module executable must not be an absolute path")
    if "/" in executable:
        portable_relative_path(executable, "module executable")
        target = (code / executable).resolve()
        if code not in target.parents or not target.is_file() or not os.access(target, os.X_OK):
            raise ValidationError("module executable is missing or not executable")
    return manifest, code


def dependency_lock(manifest: ModuleManifest) -> DependencyLock:
    """Return the already-confined and digest-verified lock as opaque provider input."""
    return DependencyLock(
        relative_path=manifest.dependency_lock,
        digest=manifest.dependency_digest,
        contents=manifest.dependency_contents,
    )


_EXCLUDED = {
    ".git",
    ".mypy_cache",
    ".omf",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "secrets",
}


def package_module(code_root: str | Path, output: str | Path) -> str:
    """Create a byte-reproducible tar, rejecting links/special files and secret areas."""
    root, destination = Path(code_root).resolve(), Path(output)
    with (
        destination.open("wb") as raw,
        tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
            relative = path.relative_to(root)
            if any(part in _EXCLUDED for part in relative.parts):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValidationError(f"unsupported package entry: {relative}")
            info = tarfile.TarInfo(relative.as_posix() + ("/" if path.is_dir() else ""))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o755 if path.is_dir() or mode & stat.S_IXUSR else 0o644
            if path.is_file():
                data = path.read_bytes()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
    return "sha256:" + hashlib.sha256(destination.read_bytes()).hexdigest()


def extract_module_package(package: str | Path, destination: str | Path) -> Path:
    """Materialize a validated module tar without tar traversal or special-file behavior."""
    target = Path(destination)
    if target.exists():
        raise ValidationError("module extraction destination already exists")
    target.mkdir(parents=True)
    try:
        with tarfile.open(package, mode="r:") as archive:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or str(path) != member.name.rstrip("/"):
                    raise ValidationError(f"unsafe module package path: {member.name}")
                output = target.joinpath(*path.parts)
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValidationError(f"unreadable module package entry: {member.name}")
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(source.read())
                else:
                    raise ValidationError(f"unsupported module package entry: {member.name}")
                os.chmod(output, member.mode & 0o777)
    except Exception:
        import shutil

        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def git_source(root: str | Path, *, allow_dirty: bool = False) -> dict[str, Any]:
    cwd = Path(root)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()
    patch = subprocess.run(
        ["git", "diff", "--binary", "HEAD"], cwd=cwd, check=True, capture_output=True
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if (patch or untracked) and not allow_dirty:
        raise ValidationError("dirty Git tree requires explicit allow_dirty policy")
    return {
        "commit": commit,
        "patch": patch,
        "untracked": sorted(untracked),
        "digest": "sha256:" + hashlib.sha256(patch).hexdigest(),
    }


def validate_fixtures(manifest: ModuleManifest) -> None:
    for fixture in manifest.fixtures:
        if "request" not in fixture or "result" not in fixture:
            raise ValidationError("fixtures require request and result")


def validate_contract(contract: Any, value: Any, name: str) -> None:
    """Validate a protocol value without including untrusted values in errors."""
    errors = sorted(
        Draft202012Validator(contract).iter_errors(value),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if errors:
        raise ValidationError(
            f"module {name} contract failed",
            details={
                "errors": [
                    {
                        "path": "$"
                        + "".join(
                            f"[{item}]" if isinstance(item, int) else f".{item}"
                            for item in error.absolute_path
                        ),
                        "constraint": str(error.validator),
                    }
                    for error in errors
                ]
            },
        )


def validate_contract_schema(contract: Any, name: str) -> None:
    """Validate an embedded, self-contained JSON Schema at admission."""
    reject_schema_references(contract, name)
    try:
        Draft202012Validator.check_schema(contract)
    except Exception as exc:
        raise ValidationError(f"{name} contract is not a valid JSON Schema") from exc


def reject_schema_references(value: Any, name: str) -> None:
    """Reject actual JSON Schema reference keywords without inspecting instance literals."""
    if isinstance(value, dict):
        if isinstance(value.get("$ref"), str) or isinstance(value.get("$dynamicRef"), str):
            raise ValidationError(f"module {name} contract references are not supported")
        for keyword, child in value.items():
            if keyword in {"const", "default", "enum", "examples"}:
                continue
            if keyword in {
                "$defs",
                "dependentSchemas",
                "patternProperties",
                "properties",
            } and isinstance(child, dict):
                for schema in child.values():
                    reject_schema_references(schema, name)
            else:
                reject_schema_references(child, name)
    elif isinstance(value, list):
        for child in value:
            reject_schema_references(child, name)
