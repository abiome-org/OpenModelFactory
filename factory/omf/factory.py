"""Integrated Open Model Factory application service."""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omf.agent import AgentControl
from omf.artifacts import ArtifactBuilder, ArtifactManifest, AtomicCheckpointPublisher
from omf.backups import create_backup
from omf.canonical import canonical_json, load_document, sha256_digest
from omf.config import ProjectPaths, load_project
from omf.data import DataService, DatasetSnapshot
from omf.database import Database, ResourceRepository
from omf.errors import (
    CapabilityError,
    ConfigurationError,
    ConflictError,
    IntegrityError,
    NotFoundError,
    OMFError,
    ValidationError,
)
from omf.events import EventStore
from omf.executors import (
    DEPLOYMENT_PROTOCOL_CAPABILITIES,
    MODULE_PROTOCOL_CAPABILITIES,
    ExecutionPlan,
    Executor,
    ExecutorRegistry,
    ResolvedExecutor,
    default_executor_registry,
)
from omf.federation import FederationBroker
from omf.ids import uuid7
from omf.lineage import LineageEdge, LineageStore
from omf.modules import (
    ModuleManifest,
    dependency_lock,
    extract_module_package,
    load_manifest,
    package_module,
    validate_contract,
    validate_contract_schema,
    validate_fixtures,
)
from omf.operations import OperationStore
from omf.policy import promotion_gate
from omf.releases import Release, ReleaseBuilder, promote_alias, verify_release
from omf.schema_registry import default_registry
from omf.sdk import ProtocolRequest, ProtocolResult
from omf.security import ApiPrincipal, ApiTokenStore, SecretStore, SigningIdentity
from omf.stores.base import ArtifactStore
from omf.stores.filesystem import FilesystemStore
from omf.stores.s3 import S3Store
from omf.sync import SyncEngine
from omf.telemetry import TelemetrySink
from omf.workloads import (
    RunState,
    Stage,
    StateStore,
    WorkloadRunner,
    project_workload,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@contextlib.contextmanager
def _operation_lease(path: Path) -> Iterator[None]:
    """Exclude concurrent workers and make a released running record detectably stale."""
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConflictError("run operation is already executing") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write_execution_record(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("wb") as output:
        output.write(canonical_json(value))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _execution_plan_digest(
    plan: ExecutionPlan, *, request_digest: str, environment_digest: str
) -> str:
    return sha256_digest(
        {
            "plan": {
                "argv": plan.argv,
                "runDir": str(plan.run_dir),
                "cwd": str(plan.cwd),
                "env": plan.env,
                "resources": plan.resources,
                "timeout": plan.timeout,
                "denyNetwork": plan.deny_network,
                "metadata": plan.metadata,
            },
            "request": request_digest,
            "environment": environment_digest,
        }
    )


class Factory:
    """One authenticated application boundary shared by CLI and HTTP interfaces."""

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        actor: str = "local-user",
        executors: ExecutorRegistry | None = None,
    ) -> None:
        if not paths.database.exists():
            raise ConfigurationError(
                "factory is not bootstrapped; run `omf bootstrap` first",
                remediation=[
                    {
                        "action": "project.bootstrap",
                        "command": "omf bootstrap --plan && omf bootstrap",
                        "description": "Inspect and then apply repository-scoped initialization.",
                    }
                ],
            )
        self.paths = paths
        self.actor = actor
        self.project = load_project(paths)
        self.namespace = str(self.project["metadata"]["namespace"])
        self.db = Database(paths.database)
        self.identity = SigningIdentity(paths.signing_key)
        self.secrets = SecretStore(self.db, paths.secret_key)
        self.api_tokens = ApiTokenStore(self.db)
        local_token = self.secrets.get("local-api-token", "api-authentication").decode()
        owners = self.project["spec"].get("owners", [])
        local_principal = self.api_tokens.register(
            local_token,
            actor=str(owners[0]) if owners else "local-user",
            scopes={"*"},
        )
        self.local_token_id = local_principal.token_id
        self.events = EventStore(self.db, self.identity)
        self.federation = FederationBroker(self.identity, database=self.db)
        self.resources = ResourceRepository(self.db)
        self.lineage = LineageStore(self.db)
        self.operations = OperationStore(self.db)
        self.local_store = FilesystemStore(paths.store)
        self.telemetry = TelemetrySink(paths.telemetry)
        self.executors = executors or default_executor_registry()
        self.agent = AgentControl(self)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Factory:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def authenticate(self, token: str) -> bool:
        """Constant-time local API token verification."""
        return self.authenticate_principal(token) is not None

    def authenticate_principal(self, token: str) -> ApiPrincipal | None:
        return self.api_tokens.authenticate(token)

    def revoke_api_token(self, token_id: str) -> None:
        if token_id == self.local_token_id:
            raise ValidationError("the bootstrap operator token cannot be revoked")
        self.api_tokens.revoke(token_id)

    def doctor(self) -> dict[str, Any]:
        """Run non-destructive readiness checks with actionable findings."""
        checks: list[dict[str, Any]] = []

        def check(name: str, function: Any, remediation: str) -> None:
            try:
                detail = function()
                checks.append({"name": name, "status": "pass", "detail": detail})
            except Exception as exc:
                checks.append(
                    {
                        "name": name,
                        "status": "fail",
                        "detail": str(exc),
                        "remediation": remediation,
                    }
                )

        check(
            "project-schema",
            lambda: default_registry.validate(self.project)["metadata"]["name"],
            "fix omf.yaml according to `omf schema show Project`",
        )
        check(
            "database-integrity",
            lambda: (
                "ok"
                if self.db.integrity_check()
                else (_ for _ in ()).throw(IntegrityError("database integrity check failed"))
            ),
            "restore .omf/metadata.db from a verified backup",
        )
        check(
            "signing-identity",
            lambda: self._check_identity(),
            "restore the signing identity or bootstrap a new project trust domain",
        )
        check(
            "secret-service",
            lambda: f"token:{len(self.secrets.get('local-api-token', 'api-authentication'))} bytes",
            "verify .omf/identity ownership and permissions",
        )
        check(
            "artifact-store",
            lambda: f"{shutil.disk_usage(self.paths.store).free} bytes free",
            "make .omf/store writable or configure another store",
        )
        check(
            "git",
            lambda: subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=self.paths.root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "initialize a Git repository and commit desired state",
        )
        check(
            "clock",
            lambda: _utc_now(),
            "configure a trusted UTC time source",
        )
        failures = sum(item["status"] == "fail" for item in checks)
        return {
            "ready": failures == 0,
            "project": self.project["metadata"]["name"],
            "profile": "local",
            "checks": checks,
            "failures": failures,
        }

    def _check_identity(self) -> str:
        value = {"probe": str(uuid7())}
        signature = self.identity.sign(value)
        self.identity.verify(value, signature)
        mode = self.paths.signing_key.stat().st_mode & 0o777
        if mode & 0o077:
            raise IntegrityError("signing key permissions are broader than 0600")
        return self.identity.key_id

    def _validate_namespace(self, value: dict[str, Any]) -> None:
        metadata = value.get("metadata", {})
        namespace = metadata.get("namespace") if isinstance(metadata, dict) else None
        if namespace != self.namespace:
            raise ValidationError(
                f"resource namespace {namespace!r} does not match project namespace "
                f"{self.namespace!r}"
            )

    def apply_resource(self, value: dict[str, Any], *, _system: bool = False) -> dict[str, Any]:
        generated_kinds = {
            "Checkpoint",
            "EvaluationResult",
            "Experiment",
            "Run",
            "RunResult",
            "SamplerState",
        }
        if value.get("kind") in generated_kinds and not _system:
            raise ValidationError(
                f"{value.get('kind')} resources are created only by the factory coordinator"
            )
        self._validate_namespace(value)
        candidate = deepcopy(value)
        metadata_value = candidate.get("metadata", {})
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        existing = [
            resource
            for resource in self.resources.list(kind=str(candidate.get("kind", "")))
            if resource["metadata"]["name"] == metadata.get("name")
            and resource["metadata"]["namespace"] == metadata.get("namespace")
        ]
        latest = (
            sorted(existing, key=lambda item: item["metadata"]["createdAt"])[-1]
            if existing
            else None
        )
        if latest is not None:
            supplied_uid = metadata.get("uid")
            if supplied_uid is not None and supplied_uid != latest["metadata"]["uid"]:
                raise ConflictError("resource name is already bound to a different uid")
            metadata["uid"] = latest["metadata"]["uid"]
        normalized = default_registry.normalize(candidate, actor=self.actor)
        metadata = normalized["metadata"]
        if latest is not None and metadata["revision"] == latest["metadata"]["revision"]:
            self._record_spec_validated(latest)
            return latest
        stored = self.resources.put(
            metadata["uid"],
            metadata["revision"],
            normalized["kind"],
            normalized,
            created_at=metadata["createdAt"],
        )
        self._record_spec_validated(stored)
        return stored

    def _record_spec_validated(self, resource: dict[str, Any]) -> None:
        self.events.append(
            type="SpecValidated",
            source=f"omf://{self.namespace}",
            subject=f"{resource['kind']}/{resource['metadata']['name']}",
            resource_uid=resource["metadata"]["uid"],
            revision=resource["metadata"]["revision"],
            actor=self.actor,
            data={"specDigest": resource["specDigest"], "kind": resource["kind"]},
            dataschema="https://schemas.omf.dev/events/spec-validated/v1",
            dedupe_revision=True,
        )

    def apply_resource_file(self, path: str | Path) -> dict[str, Any]:
        value = load_document(Path(path).read_bytes())
        if not isinstance(value, dict):
            raise ValidationError("resource file must contain one object")
        return self.apply_resource(value)

    def list_resources(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        return self.resources.list(kind=kind)

    def find_resource(self, kind: str, name: str) -> dict[str, Any]:
        matches = [
            resource
            for resource in self.resources.list(kind=kind)
            if resource["metadata"]["name"] == name
        ]
        if not matches:
            raise NotFoundError(f"{kind} resource not found: {name}")
        return sorted(matches, key=lambda item: item["metadata"]["createdAt"])[-1]

    def add_store(
        self,
        name: str,
        *,
        driver: str,
        endpoint: str,
        secret_ref: str | None = None,
        plan: bool = False,
    ) -> dict[str, Any]:
        resource = {
            "apiVersion": "omf.dev/v1alpha1",
            "kind": "ArtifactStore",
            "metadata": {"name": name, "namespace": self.namespace},
            "spec": {
                "storeType": driver,
                "location": endpoint,
                "capabilities": {},
                "config": {"secretRef": secret_ref} if secret_ref else {},
            },
        }
        default_registry.validate(resource)
        if plan:
            return {"plan": "add-store", "resource": resource, "mutates": False}
        return self.apply_resource(resource)

    def get_store(self, name: str) -> ArtifactStore:
        if name == "local":
            return self.local_store
        resource = self.find_resource("ArtifactStore", name)
        spec = resource["spec"]
        driver = spec["storeType"]
        location = str(spec["location"])
        if driver == "filesystem":
            path = Path(location)
            if not path.is_absolute():
                path = self.paths.root / path
            return FilesystemStore(path)
        if driver == "s3":
            config = spec.get("config", {})
            endpoint = config.get("endpointUrl")
            bucket, _, prefix = location.removeprefix("s3://").partition("/")
            credentials: dict[str, Any] = {}
            secret_ref = config.get("secretRef")
            if secret_ref:
                try:
                    value = json.loads(
                        self.secrets.get(str(secret_ref), "artifact-store-credentials").decode()
                    )
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ConfigurationError(
                        f"artifact store credential {secret_ref!r} must be a JSON object"
                    ) from exc
                if not isinstance(value, dict):
                    raise ConfigurationError(
                        f"artifact store credential {secret_ref!r} must be a JSON object"
                    )
                allowed = {
                    "aws_access_key_id",
                    "aws_secret_access_key",
                    "aws_session_token",
                    "region_name",
                    "use_ssl",
                    "verify",
                }
                unexpected = set(value) - allowed
                if unexpected:
                    raise ConfigurationError(
                        f"unsupported S3 credential options: {sorted(unexpected)}"
                    )
                credentials = value
            return S3Store(
                bucket,
                prefix,
                endpoint_url=endpoint,
                credential_reference=secret_ref,
                **credentials,
            )
        raise CapabilityError(f"unsupported artifact store driver: {driver}")

    def add_data(
        self,
        source: str | Path,
        *,
        name: str,
        mode: str,
        rights: dict[str, Any] | None = None,
        sample_schema: str = "application/octet-stream",
        cursor_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = DataService(self.local_store).add(
            name,
            source,
            mode,
            rights=rights,
            sample_schema=sample_schema,
            cursor_policy=cursor_policy,
        )
        extension: dict[str, Any] = {
            "mode": snapshot.mode,
            "source": (
                snapshot.artifact.manifest_digest
                if snapshot.artifact is not None
                else snapshot.source
            ),
            "cursorPolicy": snapshot.cursor_policy,
        }
        if snapshot.artifact is not None:
            extension["artifact"] = snapshot.artifact.to_dict()
            extension["manifestDigest"] = snapshot.artifact.manifest_digest
        resource = {
            "apiVersion": "omf.dev/v1alpha1",
            "kind": "DatasetSnapshot",
            "metadata": {"name": name, "namespace": self.namespace},
            "spec": {
                "sampleSchema": snapshot.sample_schema,
                "partitions": list(snapshot.partitions),
                "rights": snapshot.rights,
                "statistics": snapshot.statistics,
                "extensions": extension,
            },
        }
        stored = self.apply_resource(resource)
        metadata = stored["metadata"]
        self.events.append(
            type="ArtifactCommitted",
            source=f"omf://{self.namespace}",
            subject=f"DatasetSnapshot/{name}",
            resource_uid=metadata["uid"],
            revision=metadata["revision"],
            actor=self.actor,
            data={
                "mode": mode,
                "manifestDigest": extension.get("manifestDigest"),
                "partitions": len(snapshot.partitions),
            },
            dataschema="https://schemas.omf.dev/events/artifact-committed/v1",
        )
        self.lineage.add(
            LineageEdge(
                f"source:{sha256_digest(snapshot.source)}",
                self._resource_uri(stored),
                "wasDerivedFrom",
                "entity",
                "entity",
                attributes={"mode": mode},
            )
        )
        return stored

    def _snapshot_from_resource(self, resource: dict[str, Any]) -> DatasetSnapshot:
        spec = resource["spec"]
        extension = spec.get("extensions", {})
        artifact = extension.get("artifact")
        return DatasetSnapshot(
            resource["metadata"]["name"],
            extension["mode"],
            extension["source"],
            tuple(spec["partitions"]),
            spec["sampleSchema"],
            spec.get("rights", {}),
            spec.get("statistics", {}),
            extension.get("cursorPolicy", {}),
            ArtifactManifest.from_dict(artifact) if artifact else None,
        )

    def verify_data(self, name: str) -> bool:
        return DataService(self.local_store).verify(
            self._snapshot_from_resource(self.find_resource("DatasetSnapshot", name))
        )

    def sync(
        self,
        asset: str,
        *,
        source: str = "local",
        destination: str,
        direction: str = "push",
        concurrency: int = 4,
        plan: bool = False,
    ) -> dict[str, Any]:
        manifest_digest = asset
        if not asset.startswith("sha256:"):
            dataset_name = asset.removeprefix("dataset/")
            dataset = self.find_resource("DatasetSnapshot", dataset_name)
            try:
                manifest_digest = dataset["spec"]["extensions"]["manifestDigest"]
            except KeyError as exc:
                raise ValidationError(
                    "registered/mounted/stream data has no copyable manifest"
                ) from exc
        source_store, destination_store = self.get_store(source), self.get_store(destination)
        engine = SyncEngine()
        sync_plan = engine.plan(
            source_store,
            destination_store,
            manifest_digest,
            direction=direction,
            concurrency=concurrency,
        )
        result = {
            "manifestDigest": manifest_digest,
            "direction": direction,
            "source": source,
            "destination": destination,
            "missingChunks": [asdict(chunk) for chunk in sync_plan.missing_chunks],
            "bytes": sum(chunk.size for chunk in sync_plan.missing_chunks),
        }
        if plan:
            return {"plan": result, "mutates": False}
        manifest = engine.execute(sync_plan, source_store, destination_store)
        self.events.append(
            type="ArtifactCommitted",
            source=f"omf://{self.namespace}",
            subject=manifest_digest,
            resource_uid=str(uuid7()),
            revision=manifest.manifest_digest,
            actor=self.actor,
            data=result,
            dataschema="https://schemas.omf.dev/events/replica-committed/v1",
        )
        return {**result, "committed": True}

    def validate_module(self, manifest_path: str | Path) -> dict[str, Any]:
        manifest, code_root, package_digest, artifact_digest = self._capture_module_source(
            manifest_path
        )
        return {
            "valid": True,
            "kind": manifest.kind,
            "codeRoot": str(code_root.relative_to(self.paths.root)),
            "packageDigest": package_digest,
            "artifactManifest": artifact_digest,
            "dependencyLock": {
                "path": manifest.dependency_lock,
                "digest": manifest.dependency_digest,
                "size": len(manifest.dependency_contents),
            },
            "fixtures": len(manifest.fixtures),
            "capabilities": sorted(manifest.capabilities),
        }

    def _capture_module_source(
        self, manifest_path: str | Path, *, extract_to: Path | None = None
    ) -> tuple[ModuleManifest, Path, str, str]:
        manifest_path = Path(manifest_path).resolve()
        manifest, code_root = load_manifest(manifest_path, self.paths.root)
        validate_fixtures(manifest)
        with tempfile.NamedTemporaryFile(
            dir=self.paths.packages, suffix=".tar", delete=False
        ) as temporary:
            package_path = Path(temporary.name)
        try:
            package_digest = package_module(manifest_path.parent, package_path)
            manifest_resource_path = manifest_path
            if extract_to is not None:
                bundle_root = extract_module_package(package_path, extract_to)
                manifest_resource_path = bundle_root / "module.yaml"
                manifest, code_root = load_manifest(manifest_resource_path, bundle_root)
                validate_fixtures(manifest)
            module_resource = default_registry.load(manifest_resource_path)
            module_digest = sha256_digest(
                {
                    "apiVersion": module_resource["apiVersion"],
                    "kind": module_resource["kind"],
                    "metadata": {
                        "name": module_resource["metadata"]["name"],
                        "namespace": module_resource["metadata"]["namespace"],
                    },
                    "spec": module_resource["spec"],
                }
            )
            artifact = ArtifactBuilder(self.local_store).import_path(
                package_path,
                logical_kind="module-source",
                provenance={
                    "manifest": Path(manifest_path)
                    .resolve()
                    .relative_to(self.paths.root.resolve())
                    .as_posix(),
                    "moduleDigest": module_digest,
                    "packageDigest": package_digest,
                },
            )
        finally:
            package_path.unlink(missing_ok=True)
        return manifest, code_root, package_digest, artifact.manifest_digest

    def test_module(self, manifest_path: str | Path) -> dict[str, Any]:
        manifest, code_root = load_manifest(manifest_path, self.paths.root)
        validate_fixtures(manifest)
        resolved = self._resolve_executor(
            "local",
            {"kind": "ModuleTest", "manifest": str(Path(manifest_path).resolve())},
            {},
        )
        self._require_executor(
            resolved,
            MODULE_PROTOCOL_CAPABILITIES
            | (frozenset({"isolation:network-deny"}) if not manifest.network else frozenset()),
        )
        environment = self._prepare_module_environment(resolved.executor, manifest, code_root)
        fixtures = manifest.fixtures
        results = []
        for index, fixture in enumerate(fixtures):
            request = dict(fixture["request"])
            request.setdefault("operation", "validate")
            protocol = ProtocolRequest.model_validate(request)
            result = self._execute_module(
                manifest,
                code_root,
                protocol,
                self.paths.runs / "module-tests" / f"{Path(manifest_path).stem}-{index}",
                executor=resolved.executor,
                executor_config=resolved.config,
                environment=environment,
            )
            expected = fixture.get("result", {})
            actual = result.model_dump(mode="json")
            for key, value in expected.items():
                if actual.get(key) != value:
                    raise IntegrityError(f"module fixture {index} mismatch at {key}")
            results.append({"fixture": index, "status": result.status})
        return {"passed": len(results), "results": results}

    def _execute_module(
        self,
        manifest: ModuleManifest,
        code_root: Path,
        request: ProtocolRequest,
        run_dir: Path,
        *,
        executor: Executor,
        executor_config: dict[str, Any],
        environment: dict[str, Any],
        recovering: bool = False,
    ) -> ProtocolResult:
        validate_contract(manifest.schemas["input"], request.inputs, "input")
        validate_contract(manifest.schemas["config"], request.config, "config")
        validate_contract(manifest.schemas["state"], request.state, "state input")
        run_dir.mkdir(parents=True, exist_ok=True)
        request_path = run_dir / "request.json"
        request_bytes = canonical_json(request.model_dump(mode="json"))
        if recovering and request_path.exists():
            if request_path.read_bytes() != request_bytes:
                raise IntegrityError("recovered module request differs from the admitted request")
        else:
            request_path.write_bytes(request_bytes)
        argv = [str(item) for item in environment["command"]]
        plan = executor.plan(
            argv=argv,
            run_dir=run_dir,
            cwd=code_root,
            resources=manifest.resources,
            timeout=float(manifest.resources.get("timeout_seconds", 0)) or None,
            deny_network=not manifest.network,
            requires_result=True,
            environment=environment,
            **executor_config,
        )
        plan_digest = _execution_plan_digest(
            plan,
            request_digest=sha256_digest(request.model_dump(mode="json")),
            environment_digest=environment["digest"],
        )
        execution_record = run_dir / "controller-execution.json"
        execution_id: str | None = None
        if recovering and execution_record.exists():
            record = json.loads(execution_record.read_text())
            if record.get("planDigest") != plan_digest:
                raise IntegrityError("recovered executor plan differs from the admitted plan")
            if record.get("state") == "submitted" and isinstance(record.get("executionId"), str):
                execution_id = str(record["executionId"])
                executor.attach(execution_id, run_dir)
            elif record.get("state") == "launching":
                execution_id = executor.recover(run_dir)
                if execution_id is None:
                    raise IntegrityError("executor launch outcome is indeterminate")
                _write_execution_record(
                    execution_record,
                    {
                        "version": 1,
                        "state": "submitted",
                        "planDigest": plan_digest,
                        "executionId": execution_id,
                    },
                )
            else:
                raise IntegrityError("recovered executor record is invalid")
        if execution_id is None:
            _write_execution_record(
                execution_record,
                {"version": 1, "state": "launching", "planDigest": plan_digest},
            )
            execution_id = executor.submit(plan)
            _write_execution_record(
                execution_record,
                {
                    "version": 1,
                    "state": "submitted",
                    "planDigest": plan_digest,
                    "executionId": execution_id,
                },
            )
        while True:
            status = executor.status(execution_id)
            if status.state not in {"pending", "running"}:
                break
            time.sleep(0.05)
        result_path = run_dir / "result.json"
        if status.state != "succeeded" or not result_path.exists():
            stdout, stderr = executor.read_logs(execution_id)
            raise OMFError(
                f"module execution {status.state}: {status.reason or 'no result'}",
                details={
                    "stdout": stdout,
                    "stderr": stderr,
                },
            )
        result = ProtocolResult.model_validate_json(result_path.read_bytes())
        if result.status != "ok":
            raise OMFError(
                result.error.message if result.error else "module returned an error",
                details=result.error.details if result.error else {},
            )
        validate_contract(manifest.schemas["output"], result.outputs, "output")
        validate_contract(manifest.schemas["state"], result.state, "state output")
        return result

    @staticmethod
    def _executor_config(declaration: dict[str, Any]) -> dict[str, Any]:
        spec = declaration.get("spec", {})
        config = spec.get("config", {}) if isinstance(spec, dict) else {}
        if not isinstance(config, dict):
            raise ValidationError("declaration spec.config must be an object")
        options = config.get("executor", {})
        if not isinstance(options, dict):
            raise ValidationError("spec.config.executor must be an object")
        return dict(options)

    @staticmethod
    def _prepare_module_environment(
        executor: Executor, manifest: ModuleManifest, code_root: Path
    ) -> dict[str, Any]:
        environment = executor.prepare_environment(
            argv=manifest.argv,
            cwd=code_root,
            dependency=dependency_lock(manifest),
            deny_network=not manifest.network,
        )
        if not isinstance(environment, dict):
            raise IntegrityError("executor environment descriptor must be an object")
        command = environment.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise IntegrityError("executor environment descriptor requires command argv")
        if environment.get("dependencyDigest") != manifest.dependency_digest:
            raise IntegrityError("executor environment descriptor changed the dependency lock")
        digest = environment.get("digest")
        if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise IntegrityError("executor environment descriptor requires a canonical digest")
        try:
            canonical_json(environment)
        except (TypeError, ValueError, ValidationError) as exc:
            raise IntegrityError("executor environment descriptor must be canonical JSON") from exc
        return environment

    def _resolve_executor(
        self, name: str, declaration: dict[str, Any], config: dict[str, Any]
    ) -> ResolvedExecutor:
        return self.executors.resolve(
            name,
            project_root=self.paths.root,
            state_root=self.paths.state,
            actor=self.actor,
            declaration=declaration,
            config=config,
        )

    def _require_executor(
        self, resolved: ResolvedExecutor, required: frozenset[str]
    ) -> dict[str, Any]:
        report = self.executors.preflight(resolved, required_capabilities=required)
        if report["ready"]:
            return report
        raise CapabilityError(
            f"executor provider {resolved.provider.name!r} is not ready",
            details=report,
            remediation=[
                {
                    "action": "executor.preflight",
                    "command": "omf executor preflight <binding> --workload <workload>",
                    "description": "Inspect missing transport capabilities and host prerequisites.",
                }
            ],
        )

    def _module_requirements(self, stages: list[Stage]) -> frozenset[str]:
        required = set(MODULE_PROTOCOL_CAPABILITIES)
        for stage in stages:
            module_path = Path(stage.module)
            if not module_path.is_absolute():
                module_path = self.paths.root / module_path
            manifest, _code_root = load_manifest(module_path, self.paths.root)
            validate_fixtures(manifest)
            if not manifest.network:
                required.add("isolation:network-deny")
        return frozenset(required)

    def _admit_module_environments(
        self, stages: list[Stage], resolved: ResolvedExecutor
    ) -> dict[str, tuple[ModuleManifest, Path, dict[str, Any]]]:
        admitted: dict[str, tuple[ModuleManifest, Path, dict[str, Any]]] = {}
        for stage in stages:
            module_path = Path(stage.module)
            if not module_path.is_absolute():
                module_path = self.paths.root / module_path
            manifest, code_root = load_manifest(module_path, self.paths.root)
            validate_fixtures(manifest)
            environment = self._prepare_module_environment(resolved.executor, manifest, code_root)
            admitted[stage.name] = (manifest, code_root, environment)
        return admitted

    def executor_catalog(self) -> dict[str, Any]:
        return self.executors.catalog()

    def _project_file(self, value: str | Path, *, kind: str) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.paths.root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.paths.root.resolve())
        except ValueError as exc:
            raise ValidationError(f"{kind} must be inside the project repository") from exc
        if not resolved.is_file():
            raise ValidationError(f"{kind} file does not exist")
        return resolved

    def executor_preflight(
        self,
        binding_path: str | Path,
        *,
        workload_path: str | Path | None = None,
    ) -> dict[str, Any]:
        binding_file = self._project_file(binding_path, kind="binding")
        binding = load_document(binding_file.read_bytes())
        if not isinstance(binding, dict):
            raise ValidationError("binding must be an object")
        default_registry.validate_as(binding, "Binding")
        self._validate_namespace(binding)
        required = MODULE_PROTOCOL_CAPABILITIES
        if workload_path is not None:
            workload_file = self._project_file(workload_path, kind="workload")
            workload = load_document(workload_file.read_bytes())
            if not isinstance(workload, dict):
                raise ValidationError("workload must be an object")
            admitted = project_workload(workload)
            self._validate_namespace(workload)
            required = self._module_requirements(admitted.stages)
        name = str(binding["spec"]["executor"])
        resolved = self._resolve_executor(name, binding, self._executor_config(binding))
        report = self.executors.preflight(resolved, required_capabilities=required)
        if workload_path is not None and report["ready"]:
            try:
                self._admit_module_environments(admitted.stages, resolved)
            except Exception as exc:
                report["ready"] = False
                report["issues"].append(str(exc))
        return report

    def create_run_operation(
        self, workload_path: str | Path, binding_path: str | Path
    ) -> dict[str, Any]:
        workload = self._project_file(workload_path, kind="workload")
        binding = self._project_file(binding_path, kind="binding")
        workload_raw = load_document(workload.read_bytes())
        binding_raw = load_document(binding.read_bytes())
        if not isinstance(workload_raw, dict) or not isinstance(binding_raw, dict):
            raise ValidationError("workload and binding must be objects")
        admitted = project_workload(workload_raw)
        stages = admitted.stages
        default_registry.validate_as(binding_raw, "Binding")
        self._validate_namespace(workload_raw)
        self._validate_namespace(binding_raw)
        resolved = self._resolve_executor(
            str(binding_raw["spec"]["executor"]),
            binding_raw,
            self._executor_config(binding_raw),
        )
        self._require_executor(resolved, self._module_requirements(stages))
        self._admit_module_environments(stages, resolved)
        pinned_inputs = self._pin_stage_inputs(stages)
        model_package = self._pin_model_package(admitted.model_package_ref, stages)
        evaluation_specs = self._pin_named_resources(
            admitted.evaluation_refs, "evaluationspec/", "EvaluationSpec"
        )
        mix = self._pin_mix(admitted.mix_ref, pinned_inputs)
        module_packages: dict[str, str] = {}
        for stage in stages:
            module_path = self._project_file(stage.module, kind="module")
            manifest, _code_root = load_manifest(module_path, self.paths.root)
            validate_fixtures(manifest)
            with tempfile.NamedTemporaryFile(dir=self.paths.packages, suffix=".tar") as package:
                module_packages[stage.name] = package_module(module_path.parent, package.name)
        operation_id = str(uuid7())
        return self.operations.create(
            "run",
            {
                "workload": workload.relative_to(self.paths.root).as_posix(),
                "binding": binding.relative_to(self.paths.root).as_posix(),
                "actor": self.actor,
                "workloadDigest": sha256_digest(workload_raw),
                "bindingDigest": sha256_digest(binding_raw),
                "modulePackages": module_packages,
                "resources": {
                    "datasets": {
                        reference: self._resource_uri(resource)
                        for reference, resource in pinned_inputs.items()
                    },
                    "modelPackage": (
                        self._resource_uri(model_package) if model_package is not None else None
                    ),
                    "evaluationSpecs": [
                        self._resource_uri(resource) for resource in evaluation_specs
                    ],
                    "mix": self._resource_uri(mix) if mix is not None else None,
                },
            },
            operation_id=operation_id,
        )

    def _verify_run_request(self, request: dict[str, Any]) -> None:
        workload_path = self.paths.root / request["workload"]
        binding_path = self.paths.root / request["binding"]
        workload_raw = load_document(workload_path.read_bytes())
        binding_raw = load_document(binding_path.read_bytes())
        if (
            sha256_digest(workload_raw) != request["workloadDigest"]
            or sha256_digest(binding_raw) != request["bindingDigest"]
        ):
            raise IntegrityError("queued run desired state changed before admission")
        if not isinstance(workload_raw, dict):
            raise ValidationError("queued workload must be an object")
        for stage in project_workload(workload_raw).stages:
            module_path = self._project_file(stage.module, kind="module")
            manifest, _code_root = load_manifest(module_path, self.paths.root)
            validate_fixtures(manifest)
            with tempfile.NamedTemporaryFile(dir=self.paths.packages, suffix=".tar") as package:
                digest = package_module(module_path.parent, package.name)
            if digest != request["modulePackages"].get(stage.name):
                raise IntegrityError("queued module source changed before admission")

    def execute_run_operation(self, operation_id: str) -> dict[str, Any]:
        lease = self.paths.state / "operations" / f"{operation_id}.lock"
        with _operation_lease(lease):
            operation = self.operations.get(operation_id)
            if operation["kind"] != "run" or operation["state"] not in {
                "pending",
                "running",
                "recovering",
            }:
                raise ValidationError("operation is not an executable pending run")
            request = operation["request"]
            if request["actor"] != self.actor:
                raise ValidationError("run operation actor does not match the executing controller")
            if operation["state"] != "pending":
                reconciled = self._reconcile_completed_run(operation_id)
                if reconciled is not None:
                    return self.operations.update(
                        operation_id,
                        expected_version=operation["version"],
                        state="succeeded",
                        result=reconciled,
                    )
                try:
                    self._run_resource(operation_id)
                except IntegrityError:
                    pass
                else:
                    if (self.paths.runs / operation_id / "state.json").is_file():
                        return self._continue_run_operation(operation, recovering=True)
                message = "run outcome is indeterminate; automatic replay is disabled"
                try:
                    run_resource = self._run_resource(operation_id)
                except IntegrityError:
                    run_resource = None
                if run_resource is not None:
                    self.resources.set_status(
                        operation_id,
                        {"state": "Failed", "reason": message, "outputs": {}},
                        expected_version=None,
                    )
                self.operations.update(
                    operation_id,
                    expected_version=operation["version"],
                    state="failed",
                    error={
                        "code": "indeterminate_execution",
                        "message": message,
                        "retryable": False,
                    },
                )
                raise IntegrityError(message)
            try:
                self._verify_run_request(request)
            except OMFError as exc:
                error = exc.as_dict()["error"]
                self.operations.update(
                    operation_id,
                    expected_version=operation["version"],
                    state="failed",
                    error={
                        "code": error["code"],
                        "message": error["message"],
                        "retryable": error["retryable"],
                    },
                )
                raise
            except Exception:
                self.operations.update(
                    operation_id,
                    expected_version=operation["version"],
                    state="failed",
                    error={
                        "code": "run_admission_error",
                        "message": "run admission failed",
                        "retryable": False,
                    },
                )
                raise
            return self._continue_run_operation(operation, recovering=False)

    def _continue_run_operation(
        self, operation: dict[str, Any], *, recovering: bool
    ) -> dict[str, Any]:
        operation_id = str(operation["id"])
        request = operation["request"]
        active = self.operations.update(
            operation_id,
            expected_version=operation["version"],
            state="recovering" if recovering else "running",
            result={
                "phase": "recovery" if recovering else "admission",
                "runId": operation_id,
            },
        )
        try:
            result = self._run_impl(
                self.paths.root / request["workload"],
                self.paths.root / request["binding"],
                operation_id=operation_id,
                expected_workload_digest=request["workloadDigest"],
                expected_binding_digest=request["bindingDigest"],
                expected_module_packages=request["modulePackages"],
                expected_resources=request["resources"],
                recovering=recovering,
            )
        except OMFError as exc:
            error = exc.as_dict()["error"]
            if recovering:
                self._fail_recovered_run(operation_id, error["message"])
            self.operations.update(
                operation_id,
                expected_version=active["version"],
                state="failed",
                error={
                    "code": error["code"],
                    "message": error["message"],
                    "retryable": error["retryable"],
                },
            )
            raise
        except Exception:
            if recovering:
                self._fail_recovered_run(operation_id, "run worker failed during recovery")
            self.operations.update(
                operation_id,
                expected_version=active["version"],
                state="failed",
                error={
                    "code": "run_worker_error",
                    "message": "run worker failed",
                    "retryable": False,
                },
            )
            raise
        return self.operations.update(
            operation_id,
            expected_version=active["version"],
            state="succeeded",
            result=result,
        )

    def _fail_recovered_run(self, run_id: str, reason: str) -> None:
        run_resource = self._run_resource(run_id)
        state_store = StateStore(self.paths.runs / run_id / "state.json")
        state = state_store.read()["state"]
        if state == RunState.RUNNING.value:
            state_store.transition(RunState.RUNNING, RunState.FAILED, reason)
        elif state == RunState.RECOVERING.value:
            state_store.transition(RunState.RECOVERING, RunState.FAILED, reason)
        try:
            status, version = self.resources.get_status(run_id)
        except NotFoundError:
            status, version = {}, None
        if status.get("state") == "Failed":
            reason = str(status.get("reason", reason))
            desired_status = status
        else:
            desired_status = {"state": "Failed", "reason": reason, "outputs": {}}
        if status != desired_status:
            self.resources.set_status(run_id, desired_status, expected_version=version)
        terminal_events = self.events.query(
            run_id=run_id, resource_uid=run_id, type="RunStateChanged"
        )
        if not any(event.data.get("state") == "Failed" for event in terminal_events):
            self._run_state_event(run_resource, run_id, "Failed", reason)

    def _reconcile_completed_run(self, operation_id: str) -> dict[str, Any] | None:
        """Recover publication only from an immutable result; never rerun uncertain work."""
        runs = [
            resource
            for resource in self.resources.list(kind="Run")
            if resource["metadata"]["uid"] == operation_id
            and resource["spec"]["operationId"] == operation_id
        ]
        if not runs:
            return None
        if len(runs) != 1:
            raise IntegrityError("run resource identity is ambiguous")
        run_resource = runs[0]
        run_ref = self._resource_uri(run_resource)
        results = [
            resource
            for resource in self.resources.list(kind="RunResult")
            if resource["spec"]["runRef"] == run_ref
        ]
        if not results:
            return None
        if len(results) != 1:
            raise IntegrityError("run result identity is ambiguous")
        run_result = results[0]
        admission = run_result["spec"]["admission"]
        extensions = run_resource["spec"]["extensions"]
        result = {
            "runId": operation_id,
            "state": "Succeeded",
            "outputs": run_result["spec"]["outputs"],
            "stages": run_result["spec"]["stages"],
            "workloadDigest": admission["workloadDigest"],
            "bindingDigest": extensions["bindingDigest"],
            "resultRef": self._resource_uri(run_result),
            "reproducibility": extensions["reproducibility"],
        }
        self.lineage.add(
            LineageEdge(
                f"run:{operation_id}",
                result["resultRef"],
                "generated",
                "activity",
                "entity",
                run_id=operation_id,
            )
        )
        try:
            status, status_version = self.resources.get_status(operation_id)
        except NotFoundError:
            status, status_version = {}, None
        desired_status = {
            "state": "Succeeded",
            "reason": "completed",
            "outputs": result["outputs"],
            "resultRef": result["resultRef"],
        }
        if status != desired_status:
            self.resources.set_status(operation_id, desired_status, expected_version=status_version)
        terminal_events = self.events.query(
            run_id=operation_id, resource_uid=operation_id, type="RunStateChanged"
        )
        if not any(event.data.get("state") == "Succeeded" for event in terminal_events):
            self._run_state_event(run_resource, operation_id, "Succeeded", "completed")
        return result

    def run(self, workload_path: str | Path, binding_path: str | Path) -> dict[str, Any]:
        operation = self.create_run_operation(workload_path, binding_path)
        completed = self.execute_run_operation(operation["id"])
        return {**completed["result"], "operationId": operation["id"]}

    def _run_impl(
        self,
        workload_path: str | Path,
        binding_path: str | Path,
        *,
        operation_id: str,
        expected_workload_digest: str,
        expected_binding_digest: str,
        expected_module_packages: dict[str, str],
        expected_resources: dict[str, Any],
        recovering: bool = False,
    ) -> dict[str, Any]:
        run_id = operation_id
        run_dir = self.paths.runs / run_id
        if recovering:
            run_resource = self._run_resource(run_id)
            self._record_spec_validated(run_resource)
            self._record_run_admitted(run_resource)
            workload_resource = self._resource_by_uri(
                "WorkloadSpec", run_resource["spec"]["workloadRef"]
            )
            binding_resource = self._resource_by_uri("Binding", run_resource["spec"]["bindingRef"])
            workload_raw = workload_resource
            binding_raw = binding_resource
            if (
                workload_resource["metadata"]["revision"] != expected_workload_digest
                or binding_resource["metadata"]["revision"] != expected_binding_digest
            ):
                raise IntegrityError("recovered run does not match its admitted request")
        else:
            workload_file = self._project_file(workload_path, kind="workload")
            binding_file = self._project_file(binding_path, kind="binding")
            workload_raw = load_document(workload_file.read_bytes())
            binding_raw = load_document(binding_file.read_bytes())
            if not isinstance(workload_raw, dict) or not isinstance(binding_raw, dict):
                raise ValidationError("workload and binding must be objects")
            if (
                sha256_digest(workload_raw) != expected_workload_digest
                or sha256_digest(binding_raw) != expected_binding_digest
            ):
                raise IntegrityError("run desired state changed during admission")
        admitted = project_workload(workload_raw)
        default_registry.validate_as(binding_raw, "Binding")
        self._validate_namespace(workload_raw)
        self._validate_namespace(binding_raw)
        executor_name = str(binding_raw["spec"]["executor"])
        stages = admitted.stages
        required = self._module_requirements(stages)
        resolved_executor = self._resolve_executor(
            executor_name, binding_raw, self._executor_config(binding_raw)
        )
        self._require_executor(resolved_executor, required)
        initial_admission = (
            None if recovering else self._admit_module_environments(stages, resolved_executor)
        )
        pinned_inputs = self._pin_stage_inputs(stages, expected_resources["datasets"])
        model_package = self._pin_model_package(
            admitted.model_package_ref, stages, expected_resources["modelPackage"]
        )
        evaluation_specs = self._pin_named_resources(
            admitted.evaluation_refs,
            "evaluationspec/",
            "EvaluationSpec",
            expected_resources["evaluationSpecs"],
        )
        metric_names = [
            metric["name"] for suite in evaluation_specs for metric in suite["spec"]["metrics"]
        ]
        reserved_scores = {"compatibilityPassed", "passed"}
        if len(metric_names) != len(set(metric_names)) or reserved_scores.intersection(
            metric_names
        ):
            raise ValidationError("evaluation metric names must be unique and not reserved")
        mix = self._pin_mix(admitted.mix_ref, pinned_inputs, expected_resources["mix"])
        if not recovering:
            workload_resource = self.apply_resource(workload_raw)
            binding_resource = self.apply_resource(binding_raw)
        admitted_modules: dict[str, tuple[ModuleManifest, Path, str]] = {}
        module_digests: dict[str, str] = {}
        environments: dict[str, dict[str, Any]] = {}
        for stage in stages:
            if recovering:
                source_root = run_dir / "sources" / stage.name
                manifest, code_root = load_manifest(source_root / "module.yaml", source_root)
                with tempfile.NamedTemporaryFile(dir=self.paths.packages, suffix=".tar") as package:
                    package_digest = package_module(source_root, package.name)
                artifact_digest = run_resource["spec"]["extensions"]["moduleDigests"].get(
                    stage.name
                )
                if not isinstance(artifact_digest, str):
                    raise IntegrityError("recovered run has no admitted module source")
                source_manifest = self.local_store.read_manifest(artifact_digest)
                if not ArtifactBuilder(self.local_store).verify(source_manifest):
                    raise IntegrityError("recovered module source failed integrity verification")
                if source_manifest.digest != package_digest:
                    raise IntegrityError("recovered module source differs from admitted artifact")
            else:
                module_path = Path(stage.module)
                if not module_path.is_absolute():
                    module_path = self.paths.root / module_path
                manifest, code_root, package_digest, artifact_digest = self._capture_module_source(
                    module_path, extract_to=run_dir / "sources" / stage.name
                )
            if package_digest != expected_module_packages.get(stage.name):
                raise IntegrityError("module source changed during admission")
            environment = self._prepare_module_environment(
                resolved_executor.executor, manifest, code_root
            )
            if recovering:
                expected_environment = run_resource["spec"]["extensions"]["environments"][
                    stage.name
                ]
            else:
                assert initial_admission is not None
                expected_environment = initial_admission[stage.name][2]
            if environment["digest"] != expected_environment["digest"]:
                raise IntegrityError("module environment changed after admission")
            admitted_modules[stage.name] = (manifest, code_root, artifact_digest)
            module_digests[stage.name] = artifact_digest
            environments[stage.name] = environment
        spec = admitted.model_copy(
            update={
                "source_digest": workload_resource["metadata"]["revision"],
                "binding_digest": binding_resource["metadata"]["revision"],
                "module_digests": module_digests,
                "environments": environments,
                "input_revisions": {
                    reference: self._resource_uri(resource)
                    for reference, resource in pinned_inputs.items()
                },
                "model_package_ref": (
                    self._resource_uri(model_package) if model_package is not None else None
                ),
                "evaluation_refs": [self._resource_uri(item) for item in evaluation_specs],
                "mix_ref": self._resource_uri(mix) if mix is not None else None,
            },
        )
        state_store = StateStore(run_dir / "state.json")
        already_succeeded = False
        if recovering:
            admission = run_resource["spec"]["extensions"]
            if (
                spec.digest != admission["workloadDigest"]
                or spec.binding_digest != admission["bindingDigest"]
            ):
                raise IntegrityError("recovered run admission digest differs from durable state")
            state = state_store.verify(spec)["state"]
            if state == RunState.RUNNING.value:
                state_store.transition(RunState.RUNNING, RunState.RECOVERING)
                state = RunState.RECOVERING.value
            if state == RunState.RECOVERING.value:
                state_store.transition(RunState.RECOVERING, RunState.RUNNING)
            elif state == RunState.SUCCEEDED.value:
                already_succeeded = True
            else:
                raise IntegrityError(f"run state {state!r} cannot be recovered")
        else:
            state_store.initialize(spec)
            state_store.transition(RunState.DRAFT, RunState.VALIDATED)
            state_store.transition(RunState.VALIDATED, RunState.ADMITTED)
            state_store.transition(RunState.ADMITTED, RunState.RUNNING)
            run_resource = self.apply_resource(
                {
                    "apiVersion": "omf.dev/v1alpha1",
                    "kind": "Run",
                    "metadata": {
                        "name": f"run-{run_id}",
                        "namespace": self.namespace,
                        "uid": run_id,
                    },
                    "spec": {
                        "runId": run_id,
                        "operationId": operation_id,
                        "workloadRef": self._resource_uri(workload_resource),
                        "bindingRef": self._resource_uri(binding_resource),
                        "extensions": {
                            "workloadDigest": spec.digest,
                            "operationId": operation_id,
                            "bindingDigest": spec.binding_digest,
                            "admittedInputs": spec.input_revisions,
                            "modelPackageRef": spec.model_package_ref,
                            "evaluationRefs": spec.evaluation_refs,
                            "mixRef": spec.mix_ref,
                            "moduleDigests": spec.module_digests,
                            "environments": spec.environments,
                            "reproducibility": spec.reproducibility,
                        },
                    },
                },
                _system=True,
            )
            self._record_run_admitted(run_resource)
        if model_package is not None:
            self.lineage.add(
                LineageEdge(
                    self._resource_uri(model_package),
                    f"run:{run_id}",
                    "used",
                    "entity",
                    "activity",
                    run_id=run_id,
                )
            )
        outputs: dict[str, Any] = {
            f"{stage_name}.{name}": value
            for stage_name, stage_state in state_store.read()["stages"].items()
            if stage_state.get("status") == "succeeded"
            for name, value in stage_state.get("outputs", {}).items()
        }

        def execute(stage: Stage) -> dict[str, Any]:
            manifest, code_root, module_digest = admitted_modules[stage.name]
            environment = spec.environments[stage.name]
            self.lineage.add(
                LineageEdge(
                    f"artifact:{module_digest}",
                    f"run:{run_id}/stage:{stage.name}",
                    "used",
                    "entity",
                    "activity",
                    run_id=run_id,
                )
            )
            stage_inputs = {
                name: self._resolve_stage_input(
                    self._resolve_output_reference(reference, outputs, stages),
                    run_dir / "stages" / stage.name / "inputs" / name,
                    pinned_inputs,
                    allow_existing=recovering,
                )
                for name, reference in stage.inputs.items()
            }
            request = ProtocolRequest(
                operation=stage.operation,  # type: ignore[arg-type]
                inputs=stage_inputs,
                config=stage.config,
                context={
                    "runId": run_id,
                    "stage": stage.name,
                    "runDirectory": str(run_dir / stage.name),
                },
            )
            result = self._execute_module(
                manifest,
                code_root,
                request,
                run_dir / "stages" / stage.name,
                executor=resolved_executor.executor,
                executor_config=resolved_executor.config,
                environment=environment,
                recovering=recovering,
            )
            stage_outputs = dict(result.outputs)
            checkpoint_artifacts = [
                item for item in result.artifacts if str(item.get("kind")) == "checkpoint"
            ]
            if len(checkpoint_artifacts) > 1:
                raise ValidationError("one stage result may emit only one aggregate checkpoint")
            for artifact_index, artifact_value in enumerate(result.artifacts):
                artifact_path = Path(str(artifact_value["path"]))
                if not artifact_path.is_absolute():
                    artifact_path = run_dir / "stages" / stage.name / artifact_path
                artifact_path = artifact_path.resolve()
                allowed_root = (run_dir / "stages" / stage.name).resolve()
                if artifact_path != allowed_root and allowed_root not in artifact_path.parents:
                    raise ValidationError("module artifact path escapes the stage run directory")
                artifact = ArtifactBuilder(self.local_store).import_path(
                    artifact_path,
                    logical_kind=(
                        "checkpoint-shard"
                        if str(artifact_value.get("kind")) == "checkpoint"
                        else str(artifact_value.get("kind", "stage-output"))
                    ),
                    provenance={"runId": run_id, "stage": stage.name},
                )
                artifact_name = str(artifact_value.get("name", f"artifact-{artifact_index}"))
                if artifact_name in stage_outputs:
                    raise IntegrityError(f"stage artifact collides with output: {artifact_name}")
                artifact_digest = artifact.manifest_digest
                if str(artifact_value.get("kind")) == "checkpoint":
                    if not manifest.checkpoint:
                        raise ValidationError(
                            "module emitted a checkpoint without declaring support"
                        )
                    if not result.state:
                        raise ValidationError("checkpoint publication requires protocol state")
                    with tempfile.NamedTemporaryFile(
                        dir=run_dir / "stages" / stage.name,
                        prefix=".omf-checkpoint-state-",
                        delete=False,
                    ) as state_file:
                        state_file.write(canonical_json(result.state))
                        state_path = Path(state_file.name)
                    try:
                        state_artifact = ArtifactBuilder(self.local_store).import_path(
                            state_path,
                            logical_kind="checkpoint-state",
                            provenance={"runId": run_id, "stage": stage.name},
                        )
                    finally:
                        state_path.unlink(missing_ok=True)
                    checkpoint_components = {
                        "module-state": artifact,
                        "protocol-state": state_artifact,
                    }
                    checkpoint_replay = {
                        "status": "not-claimed",
                        "reason": "sampler-state-not-observed",
                    }
                    checkpoint_manifest = AtomicCheckpointPublisher(self.local_store).publish(
                        checkpoint_components,
                        {
                            "workload": spec.source_digest,
                            "binding": str(spec.binding_digest),
                            "module": module_digest,
                            "environment": environment["digest"],
                        },
                        checkpoint_replay,
                    )
                    artifact_digest = checkpoint_manifest.manifest_digest
                    checkpoint = self.apply_resource(
                        {
                            "apiVersion": "omf.dev/v1alpha1",
                            "kind": "Checkpoint",
                            "metadata": {
                                "name": f"checkpoint-{run_id[:8]}-{stage.name}",
                                "namespace": self.namespace,
                            },
                            "spec": {
                                "runRef": self._resource_uri(run_resource),
                                "artifactRef": artifact_digest,
                                "components": {
                                    role: component.manifest_digest
                                    for role, component in checkpoint_components.items()
                                },
                                "replay": checkpoint_replay,
                                "extensions": {"stage": stage.name},
                            },
                        },
                        _system=True,
                    )
                    self.events.append(
                        type="CheckpointCommitted",
                        source=f"omf://{self.namespace}",
                        subject=self._resource_uri(checkpoint),
                        resource_uid=checkpoint["metadata"]["uid"],
                        revision=checkpoint["metadata"]["revision"],
                        actor=self.actor,
                        run_id=run_id,
                        data={"artifactRef": artifact_digest, "stage": stage.name},
                        dataschema="https://schemas.omf.dev/events/checkpoint-committed/v1",
                        dedupe_revision=True,
                    )
                stage_outputs[artifact_name] = artifact_digest
                self.lineage.add(
                    LineageEdge(
                        f"run:{run_id}/stage:{stage.name}",
                        f"artifact:{artifact_digest}",
                        "generated",
                        "activity",
                        "entity",
                        run_id=run_id,
                    )
                )
            missing_outputs = sorted(set(stage.outputs) - stage_outputs.keys())
            if missing_outputs:
                raise IntegrityError(
                    f"stage {stage.name!r} did not produce declared outputs",
                    details={"outputs": missing_outputs},
                )
            for name, value in stage_outputs.items():
                outputs[f"{stage.name}.{name}"] = value
            return stage_outputs

        runner = WorkloadRunner(spec, state_store)
        if already_succeeded:
            result_state = state_store.read()
            if any(
                stage.get("status") == "succeeded"
                and not self._verify_stage_outputs(stage.get("outputs", {}))
                for stage in result_state["stages"].values()
            ):
                raise IntegrityError("succeeded run output evidence failed verification")
            terminal = "Succeeded"
        else:
            try:
                result_state = runner.run(execute, verify=self._verify_stage_outputs)
                state_store.transition(RunState.RUNNING, RunState.SUCCEEDED)
                terminal = "Succeeded"
            except Exception as exc:
                state_store.transition(RunState.RUNNING, RunState.FAILED, str(exc))
                terminal = "Failed"
                self.resources.set_status(
                    run_id,
                    {"state": terminal, "reason": str(exc), "outputs": outputs},
                    expected_version=None,
                )
                self._run_state_event(run_resource, run_id, terminal, str(exc))
                raise
        run_result = self.apply_resource(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "RunResult",
                "metadata": {"name": f"result-{run_id}", "namespace": self.namespace},
                "spec": {
                    "runRef": self._resource_uri(run_resource),
                    "outputs": outputs,
                    "stages": result_state["stages"],
                    "admission": {
                        "workloadDigest": spec.digest,
                        "bindingRef": self._resource_uri(binding_resource),
                        "modelPackageRef": spec.model_package_ref,
                        "moduleDigests": spec.module_digests,
                        "environments": spec.environments,
                    },
                    "extensions": {"runId": run_id},
                },
            },
            _system=True,
        )
        self.lineage.add(
            LineageEdge(
                f"run:{run_id}",
                self._resource_uri(run_result),
                "generated",
                "activity",
                "entity",
                run_id=run_id,
            )
        )
        self.resources.set_status(
            run_id,
            {
                "state": terminal,
                "reason": "completed",
                "outputs": outputs,
                "resultRef": self._resource_uri(run_result),
            },
            expected_version=None,
        )
        self._run_state_event(run_resource, run_id, terminal, "completed")
        return {
            "runId": run_id,
            "state": terminal,
            "outputs": outputs,
            "stages": result_state["stages"],
            "workloadDigest": spec.digest,
            "bindingDigest": spec.binding_digest,
            "resultRef": self._resource_uri(run_result),
            "reproducibility": spec.reproducibility,
        }

    def _pin_stage_inputs(
        self,
        stages: list[Stage],
        expected_revisions: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        pinned: dict[str, dict[str, Any]] = {}
        for stage in stages:
            for reference in stage.inputs.values():
                if reference.startswith("dataset/") and reference not in pinned:
                    if expected_revisions is not None and reference not in expected_revisions:
                        raise IntegrityError("queued dataset reference was not pinned")
                    resource = (
                        self._resource_by_uri("DatasetSnapshot", expected_revisions[reference])
                        if expected_revisions is not None
                        else self.find_resource(
                            "DatasetSnapshot", reference.removeprefix("dataset/")
                        )
                    )
                    if self._snapshot_from_resource(resource).mode != "copy":
                        raise CapabilityError(
                            "only copied dataset snapshots can be executed reproducibly"
                        )
                    snapshot = self._snapshot_from_resource(resource)
                    if snapshot.artifact is None or not ArtifactBuilder(self.local_store).verify(
                        snapshot.artifact
                    ):
                        raise IntegrityError(
                            "admitted dataset artifact failed integrity verification"
                        )
                    pinned[reference] = resource
        return pinned

    def _pin_model_package(
        self,
        reference: str | None,
        stages: list[Stage],
        expected_revision: str | None = None,
    ) -> dict[str, Any] | None:
        if reference is None:
            if expected_revision is not None:
                raise IntegrityError("queued model package does not match the workload")
            return None
        prefix = "modelpackage/"
        if not reference.startswith(prefix) or "@" in reference:
            raise ValidationError("modelPackageRef must use modelpackage/<name>")
        resource = (
            self._resource_by_uri("ModelPackage", expected_revision)
            if expected_revision is not None
            else self.find_resource("ModelPackage", reference.removeprefix(prefix))
        )
        package_spec = resource["spec"]
        signatures = package_spec["signatures"]
        for name in ("input", "output", "state"):
            contract = signatures[name]
            validate_contract_schema(contract, f"model package {name}")
            if contract.get("type") != "object":
                raise ValidationError(
                    f"model package {name} contract must describe an object for omf.module/v1"
                )
        validate_contract_schema(package_spec["architecture"]["parameterSchema"], "parameters")
        if package_spec["adapters"].get("optimized"):
            raise ValidationError("optimized model adapters are not executable by this factory")
        for vector in package_spec["compatibilityVectors"]:
            validate_contract(signatures["input"], vector["inputs"], "model package input")
            validate_contract(signatures["output"], vector["expected"], "model package output")
            for tolerance in vector.get("tolerances", {}).values():
                if not isinstance(tolerance, dict) or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(float(value))
                    or float(value) < 0
                    for value in tolerance.values()
                ):
                    raise ValidationError(
                        "model package tolerances must be finite and non-negative"
                    )
        workload_stages = {stage.name: stage for stage in stages}
        stage_outputs = {stage.name: set(stage.outputs) for stage in stages}
        adapters = package_spec["adapters"]
        for name in ("trainingReference", "inferenceReference"):
            if adapters[name]["stage"] not in stage_outputs:
                raise ValidationError(f"ModelPackage {name} references an unknown workload stage")
        training = adapters["trainingReference"]
        training_stage = workload_stages[training["stage"]]
        if training["operation"] != training_stage.operation or any(
            training_stage.config.get(key) != value for key, value in training["config"].items()
        ):
            raise ValidationError(
                "ModelPackage trainingReference does not match the workload stage"
            )
        inference = adapters["inferenceReference"]
        state_output = inference.get("stateOutput")
        if not state_output or "." not in state_output:
            raise ValidationError(
                "ModelPackage inferenceReference requires stage.output stateOutput"
            )
        stage_name, output_name = state_output.split(".", 1)
        if stage_name not in stage_outputs or output_name not in stage_outputs[stage_name]:
            raise ValidationError("ModelPackage stateOutput is not declared by the workload")
        return resource

    def _pin_named_resources(
        self,
        references: list[str],
        prefix: str,
        kind: str,
        expected_revisions: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if expected_revisions is not None and len(expected_revisions) != len(references):
            raise IntegrityError(f"queued {kind} references do not match the workload")
        resources = []
        for index, reference in enumerate(references):
            if not reference.startswith(prefix) or "@" in reference:
                raise ValidationError(f"{kind} reference must use {prefix}<name>")
            resources.append(
                self._resource_by_uri(kind, expected_revisions[index])
                if expected_revisions is not None
                else self.find_resource(kind, reference.removeprefix(prefix))
            )
        return resources

    def _pin_mix(
        self,
        reference: str | None,
        pinned_inputs: dict[str, dict[str, Any]],
        expected_revision: str | None = None,
    ) -> dict[str, Any] | None:
        if reference is None:
            if expected_revision is not None:
                raise IntegrityError("queued MixSpec does not match the workload")
            return None
        resources = self._pin_named_resources(
            [reference],
            "mixspec/",
            "MixSpec",
            [expected_revision] if expected_revision is not None else None,
        )
        mix = resources[0]
        for source in mix["spec"]["sources"]:
            dataset_ref = source.get("datasetRef")
            if dataset_ref not in pinned_inputs:
                raise ValidationError("MixSpec source is not an admitted workload dataset input")
        return mix

    @staticmethod
    def _resolve_output_reference(
        reference: str, outputs: dict[str, Any], stages: list[Stage]
    ) -> Any:
        producer = reference.partition(".")[0]
        if producer not in {stage.name for stage in stages}:
            return reference
        try:
            return outputs[reference]
        except KeyError as exc:
            raise IntegrityError(f"stage output reference is unavailable: {reference}") from exc

    def _resolve_stage_input(
        self,
        value: Any,
        target_root: Path,
        pinned_inputs: dict[str, dict[str, Any]],
        *,
        allow_existing: bool = False,
    ) -> Any:
        if not isinstance(value, str) or not value.startswith("dataset/"):
            return value
        try:
            dataset = pinned_inputs[value]
        except KeyError as exc:
            raise IntegrityError("dataset input was not pinned at admission") from exc
        snapshot = self._snapshot_from_resource(dataset)
        stage_activity = f"run:{target_root.parents[2].name}/stage:{target_root.parent.name}"
        self.lineage.add(
            LineageEdge(
                self._resource_uri(dataset),
                stage_activity,
                "used",
                "entity",
                "activity",
                run_id=target_root.parents[2].name,
            )
        )
        if snapshot.mode == "copy" and snapshot.artifact is not None:
            target = target_root / dataset["metadata"]["name"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                if not allow_existing:
                    raise IntegrityError("dataset materialization target already exists")
                if not ArtifactBuilder.verify_restored(snapshot.artifact, target):
                    raise IntegrityError("materialized dataset differs from admitted artifact")
            else:
                ArtifactBuilder(self.local_store).restore(snapshot.artifact, target)
            payload = target / "payload"
            return {
                "resource": self._resource_uri(dataset),
                "mode": snapshot.mode,
                "path": str(payload if payload.exists() else target),
                "manifestDigest": snapshot.artifact.manifest_digest,
            }
        return {
            "resource": self._resource_uri(dataset),
            "mode": snapshot.mode,
            "path": snapshot.source,
        }

    def _verify_stage_outputs(self, outputs: dict[str, Any]) -> bool:
        verifier = ArtifactBuilder(self.local_store)
        for value in outputs.values():
            if isinstance(value, str) and value.startswith("sha256:"):
                try:
                    manifest = self.local_store.read_manifest(value)
                    if not verifier.verify_graph(manifest):
                        return False
                except OMFError:
                    return False
        return True

    def _record_run_admitted(self, resource: dict[str, Any]) -> None:
        admission = resource["spec"]["extensions"]
        self.events.append(
            type="RunAdmitted",
            source=f"omf://{self.namespace}",
            subject=f"run/{resource['spec']['runId']}",
            resource_uid=resource["metadata"]["uid"],
            revision=resource["metadata"]["revision"],
            actor=self.actor,
            run_id=resource["spec"]["runId"],
            data={
                "workloadDigest": admission["workloadDigest"],
                "bindingDigest": admission["bindingDigest"],
            },
            dataschema="https://schemas.omf.dev/events/run-admitted/v1",
            workload_digest=admission["workloadDigest"],
            binding_digest=admission["bindingDigest"],
            dedupe_revision=True,
        )

    def _run_state_event(
        self, resource: dict[str, Any], run_id: str, state: str, reason: str
    ) -> None:
        self.events.append(
            type="RunStateChanged",
            source=f"omf://{self.namespace}",
            subject=f"run/{run_id}",
            resource_uid=resource["metadata"]["uid"],
            revision=resource["metadata"]["revision"],
            actor=self.actor,
            run_id=run_id,
            data={"state": state, "reason": reason},
            dataschema="https://schemas.omf.dev/events/run-state/v1",
            dedupe_revision=True,
        )

    def run_status(self, run_id: str) -> dict[str, Any]:
        status, version = self.resources.get_status(run_id)
        state_path = self.paths.runs / run_id / "state.json"
        return {
            "runId": run_id,
            "status": status,
            "statusVersion": version,
            "execution": json.loads(state_path.read_text()) if state_path.exists() else None,
        }

    def _resource_by_uri(self, kind: str, uri: str) -> dict[str, Any]:
        for resource in self.resources.list(kind=kind):
            if self._resource_uri(resource) == uri:
                return resource
        raise NotFoundError(f"pinned {kind} resource not found")

    def _run_resource(self, run_id: str) -> dict[str, Any]:
        matches = [
            resource
            for resource in self.resources.list(kind="Run")
            if resource["metadata"]["uid"] == run_id
        ]
        if len(matches) != 1:
            raise IntegrityError("run resource identity is ambiguous")
        return matches[0]

    def _run_result(self, run_id: str, status: dict[str, Any]) -> dict[str, Any]:
        reference = status.get("resultRef")
        if not isinstance(reference, str):
            raise IntegrityError("succeeded run has no immutable result")
        result = self._resource_by_uri("RunResult", reference)
        if result["spec"]["runRef"] != self._resource_uri(self._run_resource(run_id)):
            raise IntegrityError("run result does not identify the admitted run")
        return result

    @staticmethod
    def _compatibility_equal(expected: Any, actual: Any, tolerance: dict[str, Any]) -> bool:
        if isinstance(expected, bool) or isinstance(actual, bool):
            return bool(expected == actual)
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            return math.isclose(
                float(expected),
                float(actual),
                abs_tol=float(tolerance.get("absolute", 0.0)),
                rel_tol=float(tolerance.get("relative", 0.0)),
            )
        if isinstance(expected, list) and isinstance(actual, list):
            return len(expected) == len(actual) and all(
                Factory._compatibility_equal(left, right, tolerance)
                for left, right in zip(expected, actual, strict=True)
            )
        if isinstance(expected, dict) and isinstance(actual, dict):
            return expected.keys() == actual.keys() and all(
                Factory._compatibility_equal(expected[key], actual[key], tolerance)
                for key in expected
            )
        return bool(expected == actual)

    def _evaluate_model_compatibility(
        self,
        run_id: str,
        run_resource: dict[str, Any],
        run_result: dict[str, Any],
        model_package: dict[str, Any],
    ) -> tuple[bool, list[dict[str, Any]], int]:
        package_spec = model_package["spec"]
        adapter = package_spec["adapters"]["inferenceReference"]
        stage = str(adapter["stage"])
        admission = run_resource["spec"]["extensions"]
        try:
            source_digest = admission["moduleDigests"][stage]
            state = run_result["spec"]["outputs"][adapter["stateOutput"]]
        except KeyError as exc:
            raise IntegrityError(
                "model package adapter does not match admitted run outputs"
            ) from exc
        binding = self._resource_by_uri("Binding", run_resource["spec"]["bindingRef"])
        resolved = self._resolve_executor(
            str(binding["spec"]["executor"]), binding, self._executor_config(binding)
        )
        self._require_executor(resolved, MODULE_PROTOCOL_CAPABILITIES)
        source_manifest = self.local_store.read_manifest(source_digest)
        if not ArtifactBuilder(self.local_store).verify(source_manifest):
            raise IntegrityError("admitted compatibility adapter source failed verification")
        failures: list[dict[str, Any]] = []
        vectors = package_spec["compatibilityVectors"]
        with tempfile.TemporaryDirectory(dir=self.paths.packages) as temporary_name:
            temporary = Path(temporary_name)
            archive = temporary / "archive"
            ArtifactBuilder(self.local_store).restore(source_manifest, archive)
            code_root = extract_module_package(archive / "payload", temporary / "source")
            manifest, code_root = load_manifest(code_root / "module.yaml", code_root)
            self._require_executor(
                resolved,
                MODULE_PROTOCOL_CAPABILITIES
                | (frozenset({"isolation:network-deny"}) if not manifest.network else frozenset()),
            )
            environment = self._prepare_module_environment(resolved.executor, manifest, code_root)
            if environment["digest"] != admission["environments"][stage]["digest"]:
                raise IntegrityError("compatibility adapter environment differs from run admission")
            signatures = package_spec["signatures"]
            validate_contract(signatures["state"], state, "model package state")
            for index, vector in enumerate(vectors):
                validate_contract(signatures["input"], vector["inputs"], "model package input")
                request = ProtocolRequest.model_validate(
                    {
                        "operation": adapter["operation"],
                        "inputs": vector["inputs"],
                        "state": state,
                        "config": adapter["config"],
                        "context": {
                            "runId": run_id,
                            "compatibilityVector": vector["name"],
                            "inference": {
                                "method": vector["method"],
                                "seed": vector.get("seed"),
                            },
                        },
                    }
                )
                result = self._execute_module(
                    manifest,
                    code_root,
                    request,
                    self.paths.runs / run_id / "evaluations" / "compatibility" / str(index),
                    executor=resolved.executor,
                    executor_config=resolved.config,
                    environment=environment,
                )
                validate_contract(signatures["output"], result.outputs, "model package output")
                for output, expected in vector["expected"].items():
                    if output not in result.outputs or not self._compatibility_equal(
                        expected,
                        result.outputs.get(output),
                        vector.get("tolerances", {}).get(output, {}),
                    ):
                        failures.append(
                            {"kind": "compatibility", "vector": vector["name"], "output": output}
                        )
        return not failures, failures, len(vectors)

    def evaluate(self, subject: str) -> dict[str, Any]:
        """Materialize immutable evaluation evidence from evaluator stages in a run."""
        run_id = subject.removeprefix("run/")
        run_status = self.run_status(run_id)
        run_resource = self._run_resource(run_id)
        run_result = self._run_result(run_id, run_status["status"])
        outputs = run_result["spec"]["outputs"]
        passing = {
            key: value
            for key, value in outputs.items()
            if key.lower().endswith((".passed", ".pass")) and isinstance(value, bool)
        }
        failures = []
        if run_status["status"].get("state") != "Succeeded":
            failures.append({"kind": "run", "message": "source run did not succeed"})
        if not passing:
            failures.append({"kind": "protocol", "message": "no evaluator pass result found"})
        metric_scores: dict[str, Any] = {}
        for reference in run_resource["spec"]["extensions"].get("evaluationRefs", []):
            suite = self._resource_by_uri("EvaluationSpec", reference)
            for metric in suite["spec"]["metrics"]:
                value = outputs.get(metric["output"])
                metric_scores[metric["name"]] = value
                if isinstance(value, (bool, int, float)):
                    numeric = float(value)
                else:
                    failures.append(
                        {"kind": "metric", "metric": metric["name"], "message": "missing value"}
                    )
                    continue
                if "minimum" in metric and numeric < float(metric["minimum"]):
                    failures.append({"kind": "threshold", "metric": metric["name"]})
                if "maximum" in metric and numeric > float(metric["maximum"]):
                    failures.append({"kind": "threshold", "metric": metric["name"]})
        model_package_ref = run_resource["spec"]["extensions"].get("modelPackageRef")
        if model_package_ref:
            model_package = self._resource_by_uri("ModelPackage", model_package_ref)
            compatibility_passed, compatibility_failures, vector_count = (
                self._evaluate_model_compatibility(run_id, run_resource, run_result, model_package)
            )
            failures.extend(compatibility_failures)
        else:
            explicit = {
                key: value
                for key, value in outputs.items()
                if key.lower().endswith((".compatibilitypassed", ".compatibility_passed"))
                and isinstance(value, bool)
            }
            compatibility_passed = bool(explicit) and all(explicit.values())
            vector_count = 0
            if not compatibility_passed:
                failures.append(
                    {"kind": "compatibility", "message": "no model compatibility evidence found"}
                )
        passed = not failures and all(passing.values()) and compatibility_passed
        resource = self.apply_resource(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "EvaluationResult",
                "metadata": {
                    "name": f"evaluation-{run_id}",
                    "namespace": self.namespace,
                },
                "spec": {
                    "evaluationRef": f"run/{run_id}",
                    "scores": {
                        **passing,
                        **metric_scores,
                        "compatibilityPassed": compatibility_passed,
                        "passed": passed,
                    },
                    "provenance": {
                        "runId": run_id,
                        "runRef": self._resource_uri(run_resource),
                        "runResultRef": self._resource_uri(run_result),
                        "runStatusVersion": run_status["statusVersion"],
                    },
                    "uncertainty": {},
                    "failures": failures,
                    "extensions": {
                        "passed": passed,
                        "compatibilityPassed": compatibility_passed,
                        "compatibilityVectors": vector_count,
                        "evaluationRefs": run_resource["spec"]["extensions"].get(
                            "evaluationRefs", []
                        ),
                        "modelPackageRef": model_package_ref,
                        "runId": run_id,
                    },
                },
            },
            _system=True,
        )
        metadata = resource["metadata"]
        self.events.append(
            type="EvaluationCompleted",
            source=f"omf://{self.namespace}",
            subject=f"run/{run_id}",
            resource_uid=metadata["uid"],
            revision=metadata["revision"],
            actor=self.actor,
            run_id=run_id,
            data={"passed": passed, "failures": len(failures)},
            dataschema="https://schemas.omf.dev/events/evaluation-completed/v1",
        )
        self.lineage.add(
            LineageEdge(
                f"run:{run_id}",
                self._resource_uri(resource),
                "generated",
                "activity",
                "entity",
                run_id=run_id,
            )
        )
        return resource

    def create_release(
        self,
        run_id: str,
        *,
        name: str,
        intended_use: str,
        limitations: list[str] | None = None,
        promote: bool = False,
        alias: str = "candidate",
        approvals: list[str] | None = None,
        vulnerability_report: str | Path | None = None,
        evaluation_ref: str | None = None,
    ) -> dict[str, Any]:
        """Build a signed complete release and optionally move a policy-gated alias."""
        run_id = run_id.removeprefix("run/")
        run = self.run_status(run_id)
        status = run["status"]
        if status.get("state") != "Succeeded":
            raise ValidationError("only a succeeded run can produce a release")
        run_resource = self._run_resource(run_id)
        run_result = self._run_result(run_id, status)
        model_package_ref = run_resource["spec"]["extensions"].get("modelPackageRef")
        evaluations = [
            item
            for item in self.resources.list(kind="EvaluationResult")
            if item["spec"].get("evaluationRef") == f"run/{run_id}"
            and item["spec"].get("provenance", {}).get("runId") == run_id
            and item["spec"].get("provenance", {}).get("runRef") == self._resource_uri(run_resource)
            and item["spec"].get("provenance", {}).get("runResultRef")
            == self._resource_uri(run_result)
            and item["spec"].get("extensions", {}).get("runId") == run_id
            and item["spec"].get("extensions", {}).get("modelPackageRef") == model_package_ref
            and item["spec"].get("extensions", {}).get("evaluationRefs")
            == run_resource["spec"]["extensions"].get("evaluationRefs", [])
        ]
        if not evaluations:
            raise ValidationError("evaluate the run before creating a release")
        if evaluation_ref is not None:
            evaluations = [
                item
                for item in evaluations
                if evaluation_ref in {item["metadata"]["revision"], self._resource_uri(item)}
            ]
            if not evaluations:
                raise ValidationError("requested evaluation revision is not eligible for this run")
        if len(evaluations) != 1:
            raise ValidationError("multiple evaluations exist; select an exact evaluation revision")
        evaluation = evaluations[0]
        if not evaluation["spec"]["extensions"]["passed"]:
            raise ValidationError("a failing evaluation cannot produce a release")
        artifacts: list[tuple[str, str, ArtifactManifest]] = []
        for output_name, value in run_result["spec"]["outputs"].items():
            if isinstance(value, str) and value.startswith("sha256:"):
                artifacts.append((output_name, value, self.local_store.read_manifest(value)))
        artifact_digests = sorted({digest for _name, digest, _manifest in artifacts})
        if not artifact_digests:
            raise ValidationError("a release requires at least one model or output artifact")
        model_candidates = sorted(
            {
                digest
                for output_name, digest, artifact in artifacts
                if artifact.logical_kind in {"model", "model-package", "weights"}
                or output_name.lower().endswith((".model", ".modelpackage", ".weights"))
            }
        )
        if len(model_candidates) != 1:
            raise ValidationError(
                "a release requires exactly one aggregate model artifact with role model, "
                "model-package, or weights"
            )
        model_digest = model_candidates[0]
        state_candidates = sorted(
            {
                digest
                for output_name, digest, artifact in artifacts
                if artifact.logical_kind in {"checkpoint", "model-state", "state"}
                or output_name.lower().endswith((".checkpoint", ".state"))
            }
        )
        if len(state_candidates) > 1:
            raise ValidationError("a release may reference only one aggregate state artifact")
        state_digest = state_candidates[0] if state_candidates else model_digest
        admission = run_resource["spec"]["extensions"]
        module_digests = admission["moduleDigests"]
        required_scan_subjects = {model_digest, *module_digests.values()}
        vulnerability_summary, vulnerability_artifact, vulnerabilities_valid = (
            self._load_vulnerability_report(vulnerability_report, required_scan_subjects)
        )
        datasets = self.resources.list(kind="DatasetSnapshot")
        rights_valid = all(bool(item["spec"].get("rights")) for item in datasets)
        approval_list = approvals or []
        compatibility_passed = bool(evaluation["spec"]["extensions"].get("compatibilityPassed"))
        evidence = {
            "evaluation_passed": True,
            "lineage_complete": bool(self.lineage.by_run(run_id)),
            "rights_valid": rights_valid,
            "signatures_valid": self._signing_identity_valid(),
            "compatibility_passed": compatibility_passed,
            "vulnerabilities_valid": vulnerabilities_valid,
            "approvals_valid": bool(approval_list),
            "separation_of_duties": any(actor != self.actor for actor in approval_list),
        }
        decision = promotion_gate(evidence, actor=self.actor)
        if promote and decision.outcome == "deny":
            denied = [
                item["rule"] for item in decision.explanations if item.get("effect") == "deny"
            ]
            raise IntegrityError(f"promotion denied by gates: {', '.join(denied)}")
        manifest = {
            "model": {"digest": model_digest},
            "modelPackage": {"ref": model_package_ref},
            "state": {"digest": state_digest},
            "runtime": {"name": "omf.module/v1"},
            "workload": {"runId": run_id},
            "binding": {"digest": admission["bindingDigest"]},
            "dataSummary": [
                {
                    "name": item["metadata"]["name"],
                    "revision": item["metadata"]["revision"],
                    "rights": item["spec"].get("rights", {}),
                }
                for item in datasets
            ],
            "evaluations": [evaluation["metadata"]["revision"]],
            "limitations": limitations or [],
            "risk": {"promotionDecision": asdict(decision)},
            "intendedUse": intended_use,
            "prohibitedUse": ["uses not authorized by data and release policy"],
            "compatibility": {
                "moduleProtocol": "omf.module/v1",
                "passed": compatibility_passed,
                "evaluationRevision": evaluation["metadata"]["revision"],
                "vectors": evaluation["spec"]["extensions"].get("compatibilityVectors", 0),
            },
            "sbom": self._release_sbom(run_id, module_digests),
            "provenance": {"runId": run_id, "lineageComplete": evidence["lineage_complete"]},
            "vulnerabilities": vulnerability_summary,
            "deployment": {"compatible": ["batch", "service", "actor", "edge", "control"]},
            "rollback": {"compatible": True},
            "licenses": [item["spec"].get("rights", {}) for item in datasets],
        }
        signed = ReleaseBuilder(self.identity).build(manifest)
        verify_release(signed, self.identity.public_bytes)
        resource = self.apply_resource(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "Release",
                "metadata": {"name": name, "namespace": self.namespace},
                "spec": {
                    "artifacts": artifact_digests,
                    "evidence": [
                        evaluation["metadata"]["revision"],
                        *([vulnerability_artifact] if vulnerability_artifact else []),
                    ],
                    "signatures": [signed.signature],
                    "extensions": {
                        "manifest": signed.manifest,
                        "digest": signed.digest,
                        "keyId": signed.key_id,
                        "promotionDecision": asdict(decision),
                    },
                },
            },
            _system=True,
        )
        metadata = resource["metadata"]
        release_uri = self._resource_uri(resource)
        self.lineage.add(
            LineageEdge(
                f"run:{run_id}",
                release_uri,
                "generated",
                "activity",
                "entity",
                run_id=run_id,
            )
        )
        for artifact_digest in [
            *artifact_digests,
            *([vulnerability_artifact] if vulnerability_artifact else []),
        ]:
            self.lineage.add(
                LineageEdge(
                    f"artifact:{artifact_digest}",
                    release_uri,
                    "wasDerivedFrom",
                    "entity",
                    "entity",
                    run_id=run_id,
                )
            )
        self.lineage.add(
            LineageEdge(
                self._resource_uri(evaluation),
                release_uri,
                "wasDerivedFrom",
                "entity",
                "entity",
                run_id=run_id,
            )
        )
        self.events.append(
            type="ReleasePublished",
            source=f"omf://{self.namespace}",
            subject=f"Release/{name}",
            resource_uid=metadata["uid"],
            revision=metadata["revision"],
            actor=self.actor,
            run_id=run_id,
            data={"releaseDigest": signed.digest, "promoted": promote},
            dataschema="https://schemas.omf.dev/events/release-published/v1",
        )
        if promote:
            promote_alias(
                self.db,
                self.events,
                name=alias,
                uid=metadata["uid"],
                revision=metadata["revision"],
                expected_version=None,
                actor=self.actor,
                policy_decision=decision,
            )
        return resource

    def create_experiment(
        self,
        *,
        name: str,
        baseline_ref: str,
        candidate_ref: str,
        metric: str,
        direction: str,
    ) -> dict[str, Any]:
        if direction not in {"maximize", "minimize"}:
            raise ValidationError("experiment direction must be maximize or minimize")
        baseline = self._resource_by_uri("EvaluationResult", baseline_ref)
        candidate = self._resource_by_uri("EvaluationResult", candidate_ref)
        baseline_evaluations = baseline["spec"].get("extensions", {}).get("evaluationRefs", [])
        candidate_evaluations = candidate["spec"].get("extensions", {}).get("evaluationRefs", [])
        if baseline_evaluations != candidate_evaluations:
            raise ValidationError("experiment subjects use different evaluation revisions")
        try:
            baseline_score = baseline["spec"]["scores"][metric]
            candidate_score = candidate["spec"]["scores"][metric]
        except KeyError as exc:
            raise ValidationError("experiment metric is missing or non-numeric") from exc
        if (
            not isinstance(baseline_score, (int, float))
            or isinstance(baseline_score, bool)
            or not math.isfinite(float(baseline_score))
            or not isinstance(candidate_score, (int, float))
            or isinstance(candidate_score, bool)
            or not math.isfinite(float(candidate_score))
        ):
            raise ValidationError("experiment metric is missing or non-numeric")
        baseline_value = float(baseline_score)
        candidate_value = float(candidate_score)
        delta = candidate_value - baseline_value
        decision = (
            "tie"
            if delta == 0
            else "candidate"
            if (delta > 0) == (direction == "maximize")
            else "baseline"
        )
        return self.apply_resource(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "Experiment",
                "metadata": {"name": name, "namespace": self.namespace},
                "spec": {
                    "baselineRef": baseline_ref,
                    "candidateRef": candidate_ref,
                    "evaluationRefs": baseline_evaluations,
                    "metric": metric,
                    "direction": direction,
                    "decision": decision,
                    "delta": delta,
                    "extensions": {},
                },
            },
            _system=True,
        )

    def _load_vulnerability_report(
        self, report_path: str | Path | None, required_subjects: set[str]
    ) -> tuple[dict[str, Any], str | None, bool]:
        if report_path is None:
            return {"status": "not-scanned", "gate": "deny-promotion"}, None, False
        path = Path(report_path)
        report = load_document(path.read_bytes())
        if not isinstance(report, dict):
            raise ValidationError("vulnerability report must be an object")
        for field, kind in {
            "scanner": dict,
            "databaseRevision": str,
            "generatedAt": str,
            "subjects": list,
            "findings": list,
            "waivers": list,
        }.items():
            if not isinstance(report.get(field), kind):
                raise ValidationError(f"vulnerability report requires {field}")
        generated = datetime.fromisoformat(str(report["generatedAt"]).replace("Z", "+00:00"))
        if generated.tzinfo is None or generated.utcoffset() is None:
            raise ValidationError("vulnerability report time must include a timezone")
        covered = {str(item) for item in report["subjects"]}
        missing = sorted(required_subjects - covered)
        waived = {str(item) for item in report["waivers"]}
        blocking: list[str] = []
        for finding in report["findings"]:
            if not isinstance(finding, dict):
                raise ValidationError("vulnerability findings must be objects")
            identifier = str(finding.get("id", ""))
            severity = str(finding.get("severity", "unknown")).lower()
            status = str(finding.get("status", "open")).lower()
            if severity in {"critical", "high"} and status != "fixed" and identifier not in waived:
                blocking.append(identifier or "unnamed-finding")
        passed = bool(report["databaseRevision"]) and not missing and not blocking
        artifact = ArtifactBuilder(self.local_store).import_path(
            path,
            logical_kind="vulnerability-report",
            provenance={
                "scanner": report["scanner"],
                "databaseRevision": report["databaseRevision"],
            },
        )
        return (
            {
                "status": "passed" if passed else "failed",
                "scanner": report["scanner"],
                "databaseRevision": report["databaseRevision"],
                "generatedAt": report["generatedAt"],
                "reportArtifact": artifact.manifest_digest,
                "missingSubjects": missing,
                "blockingFindings": sorted(blocking),
            },
            artifact.manifest_digest,
            passed,
        )

    def _release_sbom(self, run_id: str, modules: dict[str, str]) -> dict[str, Any]:
        lock = self.paths.root / "requirements.runtime.lock"
        dependencies: list[tuple[str, str]] = []
        if lock.exists():
            for line in lock.read_text().splitlines():
                match = re.match(r"^([A-Za-z0-9_.-]+)==([^ \\]+)", line)
                if match:
                    dependencies.append((match.group(1), match.group(2)))
        packages = [
            {
                "name": name,
                "SPDXID": f"SPDXRef-Package-{index}",
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
            for index, (name, version) in enumerate(dependencies)
        ]
        packages.extend(
            {
                "name": f"omf-module-{name}",
                "SPDXID": f"SPDXRef-Module-{index}",
                "versionInfo": digest,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "OTHER",
                        "referenceType": "omf-artifact",
                        "referenceLocator": digest,
                    }
                ],
            }
            for index, (name, digest) in enumerate(sorted(modules.items()))
        )
        namespace_digest = sha256_digest({"runId": run_id, "packages": packages}).removeprefix(
            "sha256:"
        )
        return {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": f"omf-release-{run_id}",
            "documentNamespace": f"https://omf.dev/spdx/{namespace_digest}",
            "creationInfo": {
                "created": _utc_now(),
                "creators": ["Tool: Open Model Factory 0.1.0"],
            },
            "packages": packages,
        }

    def _signing_identity_valid(self) -> bool:
        probe = {"purpose": "release-gate", "time": _utc_now()}
        try:
            self.identity.verify(probe, self.identity.sign(probe))
        except IntegrityError:
            return False
        return True

    def deploy(self, deployment_path: str | Path) -> dict[str, Any]:
        """Apply a deployment resource through its explicitly selected executor provider."""
        raw = load_document(Path(deployment_path).read_bytes())
        if not isinstance(raw, dict):
            raise ValidationError("deployment file must contain one resource")
        default_registry.validate(raw)
        release_name = str(raw["spec"]["releaseRef"]).removeprefix("release/")
        release = self.find_resource("Release", release_name)
        release_extensions = release["spec"].get("extensions", {})
        signatures = release["spec"].get("signatures", [])
        if len(signatures) != 1 or release_extensions.get("keyId") != self.identity.key_id:
            raise IntegrityError("deployment release signing identity mismatch")
        verify_release(
            Release(
                manifest=release_extensions.get("manifest", {}),
                digest=str(release_extensions.get("digest", "")),
                key_id=str(release_extensions.get("keyId", "")),
                signature=str(signatures[0]),
            ),
            self.identity.public_bytes,
        )
        if release_extensions.get("promotionDecision", {}).get("outcome") != "allow":
            raise IntegrityError("deployment release has no passing promotion policy decision")
        extension = raw["spec"].get("extensions", {})
        form = extension.get("form", "service")
        command = extension.get("command")
        if form != "edge" and not command:
            raise ValidationError("non-edge deployment requires extensions.command argv")
        if command:
            resolved = self._deployment_executor(raw)
            required = DEPLOYMENT_PROTOCOL_CAPABILITIES
            if bool(extension.get("denyNetwork", False)):
                required |= frozenset({"isolation:network-deny"})
            self._require_executor(resolved, required)
        name = str(raw["metadata"]["name"])
        desired_revision = default_registry.normalize(raw, actor=self.actor)["metadata"]["revision"]
        expected_version: int | None = None
        previous_status: dict[str, Any] | None = None
        try:
            existing = self.find_resource("DeploymentSpec", name)
            previous_status, expected_version = self.resources.get_status(
                existing["metadata"]["uid"]
            )
            if previous_status.get("deploymentRevision") == desired_revision:
                current = self.deployment_status(name)
                return {
                    "deployment": current["deployment"],
                    "state": current["status"]["state"],
                    "executionId": current["status"].get("executionId"),
                }
            if previous_status.get("state") == "running":
                canceled = self.cancel_deployment(name)
                previous_status = canceled["status"]
                expected_version = int(canceled["statusVersion"])
        except NotFoundError:
            pass
        resource = self.apply_resource(raw)
        state, execution_id, run_dir, executor_name = self._launch_deployment(resource)
        self.resources.set_status(
            resource["metadata"]["uid"],
            {
                "state": state,
                "releaseRevision": release["metadata"]["revision"],
                "deploymentRevision": resource["metadata"]["revision"],
                "previousDeploymentRevision": (
                    previous_status.get("deploymentRevision") if previous_status else None
                ),
                "executionId": execution_id,
                "executor": executor_name,
                "runDirectory": str(run_dir) if run_dir else None,
            },
            expected_version=expected_version,
        )
        self.lineage.add(
            LineageEdge(
                self._resource_uri(release),
                self._resource_uri(resource),
                "wasDerivedFrom",
                "entity",
                "entity",
            )
        )
        self.events.append(
            type="DeploymentChanged",
            source=f"omf://{self.namespace}",
            subject=f"Deployment/{resource['metadata']['name']}",
            resource_uid=resource["metadata"]["uid"],
            revision=resource["metadata"]["revision"],
            actor=self.actor,
            data={"state": state, "release": release["metadata"]["revision"]},
            dataschema="https://schemas.omf.dev/events/deployment-changed/v1",
        )
        return {"deployment": resource, "state": state, "executionId": execution_id}

    def _deployment_executor(self, resource: dict[str, Any]) -> ResolvedExecutor:
        extension = resource["spec"].get("extensions", {})
        name = str(extension.get("executor", "local"))
        config = extension.get("executorConfig", {})
        if not isinstance(config, dict):
            raise ValidationError("deployment extensions.executorConfig must be an object")
        return self._resolve_executor(name, resource, config)

    def _launch_deployment(
        self, resource: dict[str, Any], *, instance: str | None = None
    ) -> tuple[str, str | None, Path | None, str | None]:
        extension = resource["spec"].get("extensions", {})
        command = extension.get("command")
        if not command:
            return "packaged", None, None, None
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ValidationError("deployment command must be an argv string array")
        run_dir = (
            self.paths.runs
            / "deployments"
            / resource["metadata"]["uid"]
            / resource["metadata"]["revision"]
        )
        if instance:
            run_dir /= instance
        resolved = self._deployment_executor(resource)
        required = DEPLOYMENT_PROTOCOL_CAPABILITIES
        if bool(extension.get("denyNetwork", False)):
            required |= frozenset({"isolation:network-deny"})
        self._require_executor(resolved, required)
        plan = resolved.executor.plan(
            argv=command,
            run_dir=run_dir,
            cwd=self.paths.root,
            resources=extension.get("resources", {}),
            timeout=float(extension.get("timeoutSeconds", 0)) or None,
            deny_network=bool(extension.get("denyNetwork", False)),
            requires_result=False,
            **resolved.config,
        )
        return "running", resolved.executor.submit(plan), run_dir, resolved.provider.name

    def deployment_status(self, name: str) -> dict[str, Any]:
        resource = self.find_resource("DeploymentSpec", name)
        uid = resource["metadata"]["uid"]
        status, version = self.resources.get_status(uid)
        desired_revision = status.get("deploymentRevision")
        if desired_revision and desired_revision != resource["metadata"]["revision"]:
            resource = self.resources.get(uid, str(desired_revision))
        execution_id = status.get("executionId")
        if status.get("state") == "running" and execution_id:
            resolved = self._deployment_executor(resource)
            status_executor = str(status.get("executor", resolved.provider.name))
            if status_executor != resolved.provider.name:
                raise IntegrityError("deployment executor does not match its immutable revision")
            run_dir = Path(str(status.get("runDirectory") or self.paths.runs / "deployments" / uid))
            resolved.executor.attach(str(execution_id), run_dir)
            observed = resolved.executor.status(str(execution_id))
            if observed.state != "running":
                updated = {
                    **status,
                    "state": observed.state,
                    "reason": observed.reason,
                    "exitCode": observed.exit_code,
                }
                try:
                    version = self.resources.set_status(uid, updated, expected_version=version)
                    status = updated
                    self._deployment_event(resource, status)
                except ConflictError:
                    status, version = self.resources.get_status(uid)
        return {"deployment": resource, "status": status, "statusVersion": version}

    def cancel_deployment(self, name: str) -> dict[str, Any]:
        current = self.deployment_status(name)
        resource = current["deployment"]
        status = current["status"]
        version = int(current["statusVersion"])
        if status.get("state") != "running":
            return current
        execution_id = status.get("executionId")
        if not execution_id:
            raise IntegrityError("running deployment has no execution identity")
        resolved = self._deployment_executor(resource)
        status_executor = str(status.get("executor", resolved.provider.name))
        if status_executor != resolved.provider.name:
            raise IntegrityError("deployment executor does not match its immutable revision")
        run_dir = Path(
            str(
                status.get("runDirectory")
                or self.paths.runs / "deployments" / resource["metadata"]["uid"]
            )
        )
        resolved.executor.attach(str(execution_id), run_dir)
        resolved.executor.cancel(str(execution_id))
        observed = resolved.executor.status(str(execution_id))
        updated = {
            **status,
            "state": observed.state,
            "reason": observed.reason,
            "exitCode": observed.exit_code,
        }
        new_version = self.resources.set_status(
            resource["metadata"]["uid"], updated, expected_version=version
        )
        self._deployment_event(resource, updated)
        return {"deployment": resource, "status": updated, "statusVersion": new_version}

    def rollback_deployment(self, name: str, *, expected_version: int) -> dict[str, Any]:
        current = self.deployment_status(name)
        if int(current["statusVersion"]) != expected_version:
            raise ConflictError("deployment status version mismatch")
        if current["status"].get("state") == "running":
            current = self.cancel_deployment(name)
            expected_version = int(current["statusVersion"])
        resource = current["deployment"]
        status = current["status"]
        previous = status.get("previousDeploymentRevision")
        if not previous:
            raise ConflictError("deployment has no previous revision to roll back")
        target = self.resources.get(resource["metadata"]["uid"], str(previous))
        release_name = str(target["spec"]["releaseRef"]).removeprefix("release/")
        release = self.find_resource("Release", release_name)
        state, execution_id, run_dir, executor_name = self._launch_deployment(
            target, instance=f"rollback-{expected_version + 1}"
        )
        updated = {
            "state": state,
            "releaseRevision": release["metadata"]["revision"],
            "deploymentRevision": target["metadata"]["revision"],
            "previousDeploymentRevision": status["deploymentRevision"],
            "executionId": execution_id,
            "executor": executor_name,
            "runDirectory": str(run_dir) if run_dir else None,
            "reason": "rollback",
        }
        new_version = self.resources.set_status(
            target["metadata"]["uid"], updated, expected_version=expected_version
        )
        self._deployment_event(target, updated)
        return {"deployment": target, "status": updated, "statusVersion": new_version}

    def _deployment_event(self, resource: dict[str, Any], status: dict[str, Any]) -> None:
        self.events.append(
            type="DeploymentChanged",
            source=f"omf://{self.namespace}",
            subject=f"Deployment/{resource['metadata']['name']}",
            resource_uid=resource["metadata"]["uid"],
            revision=resource["metadata"]["revision"],
            actor=self.actor,
            data={"state": status["state"], "release": status["releaseRevision"]},
            dataschema="https://schemas.omf.dev/events/deployment-changed/v1",
        )

    def lineage_query(
        self, subject: str, *, direction: str = "upstream", max_depth: int = 100
    ) -> list[dict[str, Any]]:
        edges = (
            self.lineage.upstream(subject, max_depth=max_depth)
            if direction == "upstream"
            else self.lineage.downstream(subject, max_depth=max_depth)
        )
        return [asdict(edge) for edge in edges]

    def backup(self, destination: str | Path) -> dict[str, Any]:
        return create_backup(self.paths, self.db, self.identity, destination)

    @staticmethod
    def _resource_uri(resource: dict[str, Any]) -> str:
        metadata = resource["metadata"]
        return (
            f"omf://{metadata['namespace']}/{resource['kind'].lower()}/"
            f"{metadata['name']}@{metadata['revision']}"
        )
