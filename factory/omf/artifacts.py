"""Immutable, model-neutral artifact manifests and builders."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO

from omf.canonical import canonical_json, sha256_digest
from omf.errors import IntegrityError, NotFoundError, ValidationError
from omf.ids import parse_digest

if TYPE_CHECKING:
    from omf.stores.base import ArtifactStore


def _digest(value: str) -> str:
    algorithm, _ = parse_digest(value)
    if algorithm != "sha256":
        raise ValidationError("artifact content requires sha256 digests")
    return value


@dataclass(frozen=True)
class ChunkDescriptor:
    digest: str
    size: int
    offset: int

    def __post_init__(self) -> None:
        _digest(self.digest)
        if self.size < 0 or self.offset < 0:
            raise ValidationError("chunk size and offset must be non-negative")


@dataclass(frozen=True)
class TreeEntry:
    path: str
    mode: int
    size: int
    digest: str
    chunks: tuple[ChunkDescriptor, ...] = ()

    def __post_init__(self) -> None:
        p = PurePosixPath(self.path)
        if not self.path or p.is_absolute() or ".." in p.parts or str(p) != self.path:
            raise ValidationError(f"unsafe artifact path: {self.path}")
        _digest(self.digest)
        if self.size < 0 or self.mode & ~0o777:
            raise ValidationError("invalid tree entry metadata")


@dataclass(frozen=True)
class ArtifactManifest:
    media_type: str
    size: int
    digest: str
    chunks: tuple[ChunkDescriptor, ...]
    logical_kind: str = "blob"
    schema_revision: str = "1"
    locations: tuple[str, ...] = ()
    entries: tuple[TreeEntry, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    rights: dict[str, Any] = field(default_factory=dict)
    security: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _digest(self.digest)
        if (
            self.size < 0
            or not self.media_type
            or not self.logical_kind
            or not self.schema_revision
        ):
            raise ValidationError("invalid artifact manifest")
        expected = 0
        for chunk in self.chunks:
            if chunk.offset != expected:
                raise ValidationError("chunks must be ordered, contiguous, and start at zero")
            expected += chunk.size
        if expected != self.size:
            raise ValidationError("chunk sizes do not equal artifact size")
        paths = [entry.path for entry in self.entries]
        if len(paths) != len(set(paths)) or paths != sorted(paths):
            raise ValidationError("tree entries must have unique sorted paths")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ArtifactManifest:
        data = dict(value)
        data["chunks"] = tuple(ChunkDescriptor(**v) for v in data.get("chunks", ()))
        data["entries"] = tuple(
            TreeEntry(**{**v, "chunks": tuple(ChunkDescriptor(**c) for c in v.get("chunks", ()))})
            for v in data.get("entries", ())
        )
        for name in ("locations",):
            data[name] = tuple(data.get(name, ()))
        return cls(**data)

    @property
    def manifest_digest(self) -> str:
        return sha256_digest(self.to_dict())


def _hash_stream(stream: BinaryIO) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    while block := stream.read(1024 * 1024):
        digest.update(block)
        size += len(block)
    return f"sha256:{digest.hexdigest()}", size


class ArtifactBuilder:
    def __init__(self, store: ArtifactStore, chunk_size: int = 8 * 1024 * 1024) -> None:
        if chunk_size <= 0:
            raise ValidationError("chunk_size must be positive")
        self.store, self.chunk_size = store, chunk_size

    def _import_file(self, path: Path) -> tuple[str, int, tuple[ChunkDescriptor, ...]]:
        whole, chunks, size = hashlib.sha256(), [], 0
        with path.open("rb") as source:
            while block := source.read(self.chunk_size):
                digest = "sha256:" + hashlib.sha256(block).hexdigest()
                descriptor = ChunkDescriptor(digest, len(block), size)
                self.store.write_chunk(digest, __import__("io").BytesIO(block), len(block))
                chunks.append(descriptor)
                whole.update(block)
                size += len(block)
        return "sha256:" + whole.hexdigest(), size, tuple(chunks)

    def import_path(self, path: str | Path, **metadata: Any) -> ArtifactManifest:
        source = Path(path)
        if source.is_symlink() or not source.exists():
            raise ValidationError("source must exist and may not be a symlink")
        entries: list[TreeEntry] = []
        if source.is_file():
            digest, size, chunks = self._import_file(source)
            manifest = ArtifactManifest(
                "application/octet-stream", size, digest, chunks, **metadata
            )
        elif source.is_dir():
            for candidate in sorted(source.rglob("*")):
                relative = candidate.relative_to(source).as_posix()
                mode = candidate.lstat().st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ValidationError(f"unsupported tree entry: {relative}")
                if stat.S_ISREG(mode):
                    digest, size, chunks = self._import_file(candidate)
                    entries.append(TreeEntry(relative, stat.S_IMODE(mode), size, digest, chunks))
            tree = [
                {"path": e.path, "mode": e.mode, "size": e.size, "digest": e.digest}
                for e in entries
            ]
            payload = canonical_json(tree)
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            chunks = (ChunkDescriptor(digest, len(payload), 0),)
            self.store.write_chunk(digest, __import__("io").BytesIO(payload), len(payload))
            manifest = ArtifactManifest(
                "application/vnd.omf.tree+json",
                len(payload),
                digest,
                chunks,
                logical_kind="directory",
                entries=tuple(entries),
                **metadata,
            )
        else:
            raise ValidationError("special files cannot be imported")
        self.store.publish_manifest(manifest)
        return manifest

    def verify(self, manifest: ArtifactManifest) -> bool:
        def verify_content(
            chunks: Iterable[ChunkDescriptor], expected: str, expected_size: int
        ) -> bool:
            calculated = hashlib.sha256()
            offset = 0
            for chunk in chunks:
                if chunk.offset != offset or not self.store.verify_chunk(chunk.digest, chunk.size):
                    return False
                offset += chunk.size
                with self.store.read_chunk(chunk.digest) as source:
                    while block := source.read(1024 * 1024):
                        calculated.update(block)
            return offset == expected_size and expected == "sha256:" + calculated.hexdigest()

        if not verify_content(manifest.chunks, manifest.digest, manifest.size):
            return False
        return all(
            verify_content(entry.chunks, entry.digest, entry.size) for entry in manifest.entries
        )

    def verify_graph(self, manifest: ArtifactManifest) -> bool:
        """Verify artifact bytes and recursively referenced checkpoint components."""
        verified: set[str] = set()
        visiting: set[str] = set()

        def visit(current: ArtifactManifest) -> bool:
            digest = current.manifest_digest
            if digest in verified:
                return True
            if digest in visiting or not self.verify(current):
                return False
            visiting.add(digest)
            if current.logical_kind == "checkpoint":
                components = current.provenance.get("components")
                if not isinstance(components, dict) or not components:
                    return False
                for reference in components.values():
                    if not isinstance(reference, str):
                        return False
                    try:
                        component = self.store.read_manifest(reference)
                    except (IntegrityError, NotFoundError, ValidationError):
                        return False
                    if not visit(component):
                        return False
            visiting.remove(digest)
            verified.add(digest)
            return True

        return visit(manifest)

    @staticmethod
    def verify_restored(manifest: ArtifactManifest, target: str | Path) -> bool:
        root = Path(target)
        try:
            root_mode = root.lstat().st_mode
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(root_mode) or stat.S_ISLNK(root_mode):
            return False

        expected_files: dict[str, TreeEntry] = {}
        expected_directories: set[str] = set()
        if manifest.logical_kind == "directory":
            expected_files = {entry.path: entry for entry in manifest.entries}
            for entry in manifest.entries:
                parent = PurePosixPath(entry.path).parent
                while str(parent) != ".":
                    expected_directories.add(str(parent))
                    parent = parent.parent
        else:
            expected_files = {"payload": TreeEntry("payload", 0, manifest.size, manifest.digest)}

        actual_files: dict[str, Path] = {}
        actual_directories: set[str] = set()
        for path in root.rglob("*"):
            mode = path.lstat().st_mode
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(mode):
                return False
            if stat.S_ISDIR(mode):
                actual_directories.add(relative)
            elif stat.S_ISREG(mode):
                actual_files[relative] = path
            else:
                return False
        if set(actual_files) != set(expected_files) or actual_directories != expected_directories:
            return False
        for relative, expected in expected_files.items():
            path = actual_files[relative]
            with path.open("rb") as stream:
                digest, size = _hash_stream(stream)
            if digest != expected.digest or size != expected.size:
                return False
            if (
                manifest.logical_kind == "directory"
                and stat.S_IMODE(path.stat().st_mode) != expected.mode
            ):
                return False
        return True

    def restore(self, manifest: ArtifactManifest, target: str | Path) -> None:
        destination = Path(target)
        parent = destination.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=parent))

        def restore_content(
            chunks: Iterable[ChunkDescriptor],
            expected_digest: str,
            expected_size: int,
            sink: BinaryIO,
        ) -> None:
            whole = hashlib.sha256()
            offset = 0
            for chunk in chunks:
                if chunk.offset != offset:
                    raise IntegrityError("artifact chunk layout check failed")
                chunk_hash = hashlib.sha256()
                size = 0
                with self.store.read_chunk(chunk.digest) as source:
                    while block := source.read(1024 * 1024):
                        size += len(block)
                        chunk_hash.update(block)
                        whole.update(block)
                        sink.write(block)
                actual = "sha256:" + chunk_hash.hexdigest()
                if size != chunk.size or actual != chunk.digest:
                    raise IntegrityError("artifact chunk integrity check failed")
                offset += size
            if offset != expected_size or "sha256:" + whole.hexdigest() != expected_digest:
                raise IntegrityError("artifact payload integrity check failed")

        try:
            if manifest.entries:
                with tempfile.TemporaryFile() as index_sink:
                    restore_content(manifest.chunks, manifest.digest, manifest.size, index_sink)
                for entry in manifest.entries:
                    output = temporary / entry.path
                    if temporary.resolve() not in output.resolve().parents:
                        raise ValidationError("tree path escapes destination")
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with output.open("wb") as output_sink:
                        restore_content(entry.chunks, entry.digest, entry.size, output_sink)
                    os.chmod(output, entry.mode)
            else:
                output = temporary / "payload"
                with output.open("wb") as output_sink:
                    restore_content(manifest.chunks, manifest.digest, manifest.size, output_sink)
            if destination.exists():
                raise ValidationError("restore destination already exists")
            os.replace(temporary, destination)
        except Exception:
            import shutil

            shutil.rmtree(temporary, ignore_errors=True)
            raise


class AtomicCheckpointPublisher:
    """Publish a role-mapped checkpoint only after every component is verified."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def publish(
        self,
        components: dict[str, ArtifactManifest],
        context: dict[str, str],
        replay: dict[str, str],
    ) -> ArtifactManifest:
        if not components or any(
            re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", role) is None for role in components
        ):
            raise ValidationError("checkpoint components require normalized role names")
        if replay.get("status") not in {"not-claimed", "bound"}:
            raise ValidationError("checkpoint replay status must be explicit")
        if replay["status"] == "not-claimed" and not replay.get("reason"):
            raise ValidationError("checkpoint replay non-claim requires a reason")
        if replay["status"] == "bound" and not all(
            replay.get(field) for field in ("samplerStateRef", "mixRef")
        ):
            raise ValidationError("bound checkpoint replay requires sampler and mix revisions")
        verifier = ArtifactBuilder(self.store)
        if not all(
            verifier.verify(component)
            and self.store.read_manifest(component.manifest_digest) == component
            for component in components.values()
        ):
            raise IntegrityError("checkpoint component verification failed")
        component_refs = {
            role: component.manifest_digest for role, component in sorted(components.items())
        }
        payload = canonical_json(
            {"components": component_refs, "context": context, "replay": replay}
        )
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        self.store.write_chunk(digest, __import__("io").BytesIO(payload), len(payload))
        manifest = ArtifactManifest(
            "application/vnd.omf.checkpoint+json",
            len(payload),
            digest,
            (ChunkDescriptor(digest, len(payload), 0),),
            logical_kind="checkpoint",
            provenance={
                "components": component_refs,
                "context": context,
                "replay": replay,
            },
        )
        self.store.publish_manifest(manifest)
        return manifest
