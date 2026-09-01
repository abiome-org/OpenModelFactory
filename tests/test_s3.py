import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from omf.artifacts import ArtifactManifest, ChunkDescriptor
from omf.errors import ConflictError, IntegrityError, NotFoundError
from omf.stores.s3 import S3Store


class ClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class Client:
    def __init__(self):
        self.objects = {}

    def head_object(self, *, Bucket, Key):
        del Bucket
        if Key not in self.objects:
            raise ClientError("404")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, *, Bucket, Key, Range=None):
        del Bucket
        if Key not in self.objects:
            raise ClientError("NoSuchKey")
        value = self.objects[Key]
        if Range:
            start, end = Range.removeprefix("bytes=").split("-")
            value = value[int(start) : int(end) + 1 if end else None]
        return {"Body": io.BytesIO(value)}

    def upload_fileobj(self, source, bucket, key, ExtraArgs):
        del bucket, ExtraArgs
        self.objects[key] = source.read()

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        del Bucket, kwargs
        if Key in self.objects:
            raise ClientError("PreconditionFailed")
        self.objects[Key] = Body

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class Paginator:
            def paginate(self, *, Bucket, Prefix):
                del Bucket
                return [
                    {"Contents": [{"Key": key} for key in client.objects if key.startswith(Prefix)]}
                ]

        return Paginator()


@pytest.fixture
def s3(monkeypatch):
    client = Client()

    def importer(name):
        if name == "boto3":
            return SimpleNamespace(client=lambda *_args, **_kwargs: client)
        if name == "botocore.exceptions":
            return SimpleNamespace(ClientError=ClientError)
        raise ImportError(name)

    monkeypatch.setattr("omf.stores.s3.importlib.import_module", importer)
    return S3Store("bucket", "prefix", endpoint_url="https://object.example"), client


def _manifest(payload=b"payload"):
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    return ArtifactManifest(
        "application/octet-stream",
        len(payload),
        digest,
        (ChunkDescriptor(digest, len(payload), 0),),
    )


def test_s3_chunk_manifest_range_listing_and_idempotence(s3):
    store, _client = s3
    manifest = _manifest()
    chunk = manifest.chunks[0]
    assert not store.has_chunk(chunk.digest)
    store.write_chunk(chunk.digest, io.BytesIO(b"payload"), chunk.size)
    store.write_chunk(chunk.digest, io.BytesIO(b"ignored"), chunk.size)
    assert store.verify_chunk(chunk.digest, chunk.size)
    assert store.read_chunk(chunk.digest, 1, 3).read() == b"ayl"
    assert store.publish_manifest(manifest) == manifest.manifest_digest
    assert store.publish_manifest(manifest) == manifest.manifest_digest
    assert store.read_manifest(manifest.manifest_digest) == manifest
    assert list(store.list_manifests()) == [manifest.manifest_digest]
    assert "credential" not in repr(store)
    assert store.config()["endpoint_url"] == "https://object.example"


def test_s3_rejects_corruption_conflict_and_missing(s3):
    store, client = s3
    manifest = _manifest()
    with pytest.raises(IntegrityError):
        store.write_chunk(manifest.digest, io.BytesIO(b"wrong"), len(b"wrong"))
    with pytest.raises(NotFoundError):
        store.read_chunk(manifest.digest)
    store.write_chunk(manifest.digest, io.BytesIO(b"payload"))
    key = store._key("blobs", manifest.digest)
    client.objects[key] = b"corrupt"
    assert not store.verify_chunk(manifest.digest)
    store.publish_manifest(manifest)
    manifest_key = store._key("manifests", manifest.manifest_digest, ".json")
    client.objects[manifest_key] = json.dumps({"different": True}).encode()
    with pytest.raises(ConflictError):
        store.publish_manifest(manifest)


def test_s3_optional_dependency_error(monkeypatch):
    monkeypatch.setattr(
        "omf.stores.s3.importlib.import_module",
        lambda name: (_ for _ in ()).throw(ImportError(name)),
    )
    with pytest.raises(Exception, match="requires installation"):
        S3Store("bucket")
