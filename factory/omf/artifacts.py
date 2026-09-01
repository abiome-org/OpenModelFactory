"""Immutable, model-neutral artifact manifests and builders."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO

from omf.canonical import canonical_json, sha256_digest
from omf.errors import IntegrityError, ValidationError
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
        def verify_content(chunks: Iterable[ChunkDescriptor], expected: str) -> bool:
            calculated = hashlib.sha256()
            for chunk in chunks:
                if not self.store.verify_chunk(chunk.digest, chunk.size):
                    return False
                with self.store.read_chunk(chunk.digest) as source:
                    while block := source.read(1024 * 1024):
                        calculated.update(block)
            return expected == "sha256:" + calculated.hexdigest()

        if not verify_content(manifest.chunks, manifest.digest):
            return False
        return all(verify_content(entry.chunks, entry.digest) for entry in manifest.entries)

    def restore(self, manifest: ArtifactManifest, target: str | Path) -> None:
        destination = Path(target)
        parent = destination.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=parent))
        try:
            if manifest.entries:
                for entry in manifest.entries:
                    output = temporary / entry.path
                    if temporary.resolve() not in output.resolve().parents:
                        raise ValidationError("tree path escapes destination")
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with output.open("wb") as sink:
                        for chunk in entry.chunks:
                            with self.store.read_chunk(chunk.digest) as source:
                                while block := source.read(1024 * 1024):
                                    sink.write(block)
                    os.chmod(output, entry.mode)
            else:
                output = temporary / "payload"
                with output.open("wb") as sink:
                    for chunk in manifest.chunks:
                        with self.store.read_chunk(chunk.digest) as source:
                            while block := source.read(1024 * 1024):
                                sink.write(block)
            if destination.exists():
                raise ValidationError("restore destination already exists")
            os.replace(temporary, destination)
        except Exception:
            import shutil

            shutil.rmtree(temporary, ignore_errors=True)
            raise


class AtomicCheckpointPublisher:
    """Publish a checkpoint only after every shard is independently verified."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def publish(
        self, shards: Iterable[ArtifactManifest], state_references: dict[str, str]
    ) -> ArtifactManifest:
        shard_list = tuple(shards)
        if not all(
            all(self.store.verify_chunk(c.digest, c.size) for c in s.chunks) for s in shard_list
        ):
            raise IntegrityError("checkpoint shard verification failed")
        payload = canonical_json(
            {"shards": [s.manifest_digest for s in shard_list], "state": state_references}
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
                "stateReferences": state_references,
                "shards": [s.manifest_digest for s in shard_list],
            },
        )
        self.store.publish_manifest(manifest)
        return manifest
