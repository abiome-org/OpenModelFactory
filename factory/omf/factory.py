"""Integrated Open Model Factory application service."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omf.agent import AgentControl
from omf.artifacts import ArtifactBuilder, ArtifactManifest
from omf.canonical import canonical_json, load_document, sha256_digest
from omf.config import ProjectPaths, load_project
from omf.conformance import build_report, verify_report
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
    extract_module_package,
    load_manifest,
    package_module,
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
from omf.workloads import RunState, Stage, StateStore, WorkloadRunner, WorkloadSpec


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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

    def apply_resource(self, value: dict[str, Any]) -> dict[str, Any]:
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
            return latest
        stored = self.resources.put(
            metadata["uid"],
            metadata["revision"],
            normalized["kind"],
            normalized,
            created_at=metadata["createdAt"],
        )
        self.events.append(
            type="SpecValidated",
            source=f"omf://{self.namespace}",
            subject=f"{stored['kind']}/{metadata['name']}",
            resource_uid=metadata["uid"],
            revision=metadata["revision"],
            actor=self.actor,
            data={"specDigest": stored["specDigest"], "kind": stored["kind"]},
            dataschema="https://schemas.omf.dev/events/spec-validated/v1",
        )
        return stored

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
            "source": snapshot.source,
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
            "fixtures": len(manifest.fixtures),
            "capabilities": sorted(manifest.capabilities),
        }

    def _capture_module_source(
        self, manifest_path: str | Path, *, extract_to: Path | None = None
    ) -> tuple[ModuleManifest, Path, str, str]:
        manifest, code_root = load_manifest(manifest_path, self.paths.root)
        validate_fixtures(manifest)
        with tempfile.NamedTemporaryFile(
            dir=self.paths.packages, suffix=".tar", delete=False
        ) as temporary:
            package_path = Path(temporary.name)
        try:
            package_digest = package_module(code_root, package_path)
            artifact = ArtifactBuilder(self.local_store).import_path(
                package_path,
                logical_kind="module-source",
                provenance={"manifest": str(Path(manifest_path).resolve())},
            )
            if extract_to is not None:
                code_root = extract_module_package(package_path, extract_to)
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
        fixtures = manifest.fixtures or [{"request": {"operation": "validate"}, "result": {}}]
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
    ) -> ProtocolResult:
        run_dir.mkdir(parents=True, exist_ok=True)
        request_path = run_dir / "request.json"
        request_path.write_bytes(canonical_json(request.model_dump(mode="json")))
        argv = list(manifest.argv)
        if "/" in argv[0]:
            argv[0] = str((code_root / argv[0]).resolve())
        plan = executor.plan(
            argv=argv,
            run_dir=run_dir,
            cwd=code_root,
            resources=manifest.resources,
            timeout=float(manifest.resources.get("timeout_seconds", 0)) or None,
            deny_network=not manifest.network,
            requires_result=True,
            **executor_config,
        )
        execution_id = executor.submit(plan)
        while True:
            status = executor.status(execution_id)
            if status.state not in {"pending", "running"}:
                break
            time.sleep(0.05)
        result_path = run_dir / "result.json"
        if status.state != "succeeded" or not result_path.exists():
            stdout, stderr = executor.logs(execution_id)
            raise OMFError(
                f"module execution {status.state}: {status.reason or 'no result'}",
                details={
                    "stdout": stdout.read_text(errors="replace")[-4000:],
                    "stderr": stderr.read_text(errors="replace")[-4000:],
                },
            )
        result = ProtocolResult.model_validate_json(result_path.read_bytes())
        if result.status != "ok":
            raise OMFError(
                result.error.message if result.error else "module returned an error",
                details=result.error.details if result.error else {},
            )
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

    @staticmethod
    def _stages(workload: dict[str, Any]) -> list[Stage]:
        stages_value = workload.get("stages")
        if stages_value is None and isinstance(workload.get("spec"), dict):
            stages_value = workload["spec"].get("graph", {}).get("stages")
        stages = [Stage.model_validate(stage) for stage in stages_value or []]
        if not stages:
            raise ValidationError("workload has no stages")
        return stages

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
        default_registry.validate(binding)
        self._validate_namespace(binding)
        required = MODULE_PROTOCOL_CAPABILITIES
        if workload_path is not None:
            workload_file = self._project_file(workload_path, kind="workload")
            workload = load_document(workload_file.read_bytes())
            if not isinstance(workload, dict):
                raise ValidationError("workload must be an object")
            required = self._module_requirements(self._stages(workload))
        name = str(binding["spec"]["executor"])
        resolved = self._resolve_executor(name, binding, self._executor_config(binding))
        return self.executors.preflight(resolved, required_capabilities=required)

    def run(self, workload_path: str | Path, binding_path: str | Path) -> dict[str, Any]:
        workload_raw = load_document(Path(workload_path).read_bytes())
        binding_raw = load_document(Path(binding_path).read_bytes())
        if not isinstance(workload_raw, dict) or not isinstance(binding_raw, dict):
            raise ValidationError("workload and binding must be objects")
        default_registry.validate(binding_raw)
        self._validate_namespace(binding_raw)
        executor_name = str(binding_raw["spec"]["executor"])
        stages = self._stages(workload_raw)
        required = self._module_requirements(stages)
        resolved_executor = self._resolve_executor(
            executor_name, binding_raw, self._executor_config(binding_raw)
        )
        self._require_executor(resolved_executor, required)
        run_id = str(uuid7())
        run_dir = self.paths.runs / run_id
        admitted_modules: dict[str, tuple[ModuleManifest, Path, str]] = {}
        module_digests: dict[str, str] = {}
        for stage in stages:
            module_path = Path(stage.module)
            if not module_path.is_absolute():
                module_path = self.paths.root / module_path
            manifest, code_root, _package_digest, artifact_digest = self._capture_module_source(
                module_path, extract_to=run_dir / "sources" / stage.name
            )
            admitted_modules[stage.name] = (manifest, code_root, artifact_digest)
            module_digests[stage.name] = artifact_digest
        spec = WorkloadSpec(
            stages=stages,
            binding_digest=sha256_digest(binding_raw),
            module_digests=module_digests,
        )
        state_store = StateStore(run_dir / "state.json")
        state_store.initialize(spec)
        state_store.transition(RunState.DRAFT, RunState.VALIDATED)
        state_store.transition(RunState.VALIDATED, RunState.ADMITTED)
        state_store.transition(RunState.ADMITTED, RunState.RUNNING)
        run_resource = self.apply_resource(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "Run",
                "metadata": {"name": f"run-{run_id}", "namespace": self.namespace, "uid": run_id},
                "spec": {
                    "runId": run_id,
                    "workloadRef": str(Path(workload_path).resolve()),
                    "bindingRef": str(Path(binding_path).resolve()),
                    "extensions": {
                        "workloadDigest": spec.digest,
                        "bindingDigest": spec.binding_digest,
                    },
                },
            }
        )
        self.events.append(
            type="RunAdmitted",
            source=f"omf://{self.namespace}",
            subject=f"run/{run_id}",
            resource_uid=run_resource["metadata"]["uid"],
            revision=run_resource["metadata"]["revision"],
            actor=self.actor,
            run_id=run_id,
            data={"workloadDigest": spec.digest, "bindingDigest": spec.binding_digest},
            dataschema="https://schemas.omf.dev/events/run-admitted/v1",
            workload_digest=spec.digest,
            binding_digest=spec.binding_digest,
        )
        outputs: dict[str, Any] = {}

        def execute(stage: Stage) -> dict[str, Any]:
            manifest, code_root, module_digest = admitted_modules[stage.name]
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
                    outputs.get(reference, reference), run_dir / "stages" / stage.name / "inputs"
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
            )
            stage_outputs = dict(result.outputs)
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
                    logical_kind=str(artifact_value.get("kind", "stage-output")),
                    provenance={"runId": run_id, "stage": stage.name},
                )
                artifact_name = str(artifact_value.get("name", f"artifact-{artifact_index}"))
                stage_outputs[artifact_name] = artifact.manifest_digest
                self.lineage.add(
                    LineageEdge(
                        f"run:{run_id}/stage:{stage.name}",
                        f"artifact:{artifact.manifest_digest}",
                        "generated",
                        "activity",
                        "entity",
                        run_id=run_id,
                    )
                )
            for name, value in stage_outputs.items():
                outputs[f"{stage.name}.{name}"] = value
            return stage_outputs

        runner = WorkloadRunner(spec, state_store)
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
        self.resources.set_status(
            run_id,
            {"state": terminal, "reason": "completed", "outputs": outputs},
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
        }

    def _resolve_stage_input(self, value: Any, target_root: Path) -> Any:
        if not isinstance(value, str) or not value.startswith("dataset/"):
            return value
        dataset = self.find_resource("DatasetSnapshot", value.removeprefix("dataset/"))
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
            if not target.exists():
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
        for value in outputs.values():
            if isinstance(value, str) and value.startswith("sha256:"):
                try:
                    self.local_store.read_manifest(value)
                except NotFoundError:
                    return False
        return True

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

    def evaluate(self, subject: str) -> dict[str, Any]:
        """Materialize immutable evaluation evidence from evaluator stages in a run."""
        run_id = subject.removeprefix("run/")
        run_status = self.run_status(run_id)
        outputs = run_status["status"].get("outputs", {})
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
        passed = not failures and all(passing.values())
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
                    "scores": {**passing, "passed": passed},
                    "provenance": {
                        "runId": run_id,
                        "runStatusVersion": run_status["statusVersion"],
                    },
                    "uncertainty": {},
                    "failures": failures,
                    "extensions": {"passed": passed, "runId": run_id},
                },
            }
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
    ) -> dict[str, Any]:
        """Build a signed complete release and optionally move a policy-gated alias."""
        run_id = run_id.removeprefix("run/")
        run = self.run_status(run_id)
        status = run["status"]
        if status.get("state") != "Succeeded":
            raise ValidationError("only a succeeded run can produce a release")
        evaluations = [
            item
            for item in self.resources.list(kind="EvaluationResult")
            if item["spec"].get("extensions", {}).get("runId") == run_id
        ]
        if not evaluations:
            raise ValidationError("evaluate the run before creating a release")
        evaluation = evaluations[-1]
        if not evaluation["spec"]["extensions"]["passed"]:
            raise ValidationError("a failing evaluation cannot produce a release")
        artifacts: list[tuple[str, str, ArtifactManifest]] = []
        for output_name, value in status.get("outputs", {}).items():
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
        execution = run.get("execution") or {}
        module_digests = execution.get("digests", {}).get("modules", {})
        required_scan_subjects = {model_digest, *module_digests.values()}
        vulnerability_summary, vulnerability_artifact, vulnerabilities_valid = (
            self._load_vulnerability_report(vulnerability_report, required_scan_subjects)
        )
        datasets = self.resources.list(kind="DatasetSnapshot")
        rights_valid = all(bool(item["spec"].get("rights")) for item in datasets)
        approval_list = approvals or []
        stage_states = execution.get("stages", {})
        conformance_passed = bool(stage_states) and all(
            stage.get("status") == "succeeded" for stage in stage_states.values()
        )
        evidence = {
            "evaluation_passed": True,
            "lineage_complete": bool(self.lineage.by_run(run_id)),
            "rights_valid": rights_valid,
            "signatures_valid": self._signing_identity_valid(),
            "conformance_passed": conformance_passed,
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
            "state": {"digest": state_digest},
            "runtime": {"name": "omf.module/v1"},
            "workload": {"runId": run_id},
            "binding": {"digest": self.run_status(run_id)["execution"]["digests"]["binding"]},
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
            "conformance": {
                "moduleProtocol": "omf.module/v1",
                "passed": conformance_passed,
                "stages": stage_states,
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
            }
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
        destination_path = Path(destination).resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        self.db.backup(destination_path)
        return {
            "path": str(destination_path),
            "size": destination_path.stat().st_size,
            "integrity": Database(destination_path).integrity_check(),
        }

    def create_conformance_report(
        self, evidence_path: str | Path, output_path: str | Path
    ) -> dict[str, Any]:
        evidence = load_document(Path(evidence_path).read_bytes())
        if not isinstance(evidence, dict):
            raise ValidationError("conformance evidence must be an object")
        specification = self.paths.root / "SPEC.md"
        spec_revision = "sha256:" + hashlib.sha256(specification.read_bytes()).hexdigest()
        signed = build_report(evidence, identity=self.identity, spec_revision=spec_revision)
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(canonical_json(signed))
        artifact = ArtifactBuilder(self.local_store).import_path(
            destination,
            logical_kind="conformance-report",
            provenance={"keyId": self.identity.key_id, "reportDigest": signed["digest"]},
        )
        self.events.append(
            type="ConformanceMeasured",
            source=f"omf://{self.namespace}",
            subject=str(signed["digest"]),
            resource_uid=str(uuid7()),
            revision=str(signed["digest"]),
            actor=self.actor,
            data={
                "profilesClaimed": signed["report"]["profilesClaimed"],
                "profilesDenied": signed["report"]["profilesDenied"],
                "artifactManifest": artifact.manifest_digest,
            },
            dataschema="https://schemas.omf.dev/events/conformance-measured/v1",
        )
        return {**signed, "artifactManifest": artifact.manifest_digest, "path": str(destination)}

    def verify_conformance_report(self, report_path: str | Path) -> dict[str, Any]:
        value = load_document(Path(report_path).read_bytes())
        if not isinstance(value, dict):
            raise ValidationError("signed conformance report must be an object")
        return verify_report(value, self.identity.public_bytes)

    @staticmethod
    def _resource_uri(resource: dict[str, Any]) -> str:
        metadata = resource["metadata"]
        return (
            f"omf://{metadata['namespace']}/{resource['kind'].lower()}/"
            f"{metadata['name']}@{metadata['revision']}"
        )
