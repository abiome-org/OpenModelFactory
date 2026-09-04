"""Authenticated FastAPI surface for the Open Model Factory service."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from omf import __version__
from omf.config import ProjectPaths
from omf.errors import AuthorizationError, OMFError
from omf.executors import ExecutorRegistry
from omf.factory import Factory
from omf.schema_registry import default_registry


class StoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    driver: str
    endpoint: str
    secret_ref: str | None = None
    plan: bool = False


class DataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    name: str
    mode: str = "copy"
    rights: dict[str, Any] = Field(default_factory=dict)
    sample_schema: str = "application/octet-stream"
    cursor_policy: dict[str, Any] = Field(default_factory=dict)


class DataRevocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1024)


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset: str
    source: str = "local"
    destination: str
    direction: str = "push"
    concurrency: int = Field(default=4, ge=1, le=256)
    plan: bool = False


class ModuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: str
    binding: str | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workload: str
    binding: str = "bindings/local.yaml"
    detach: bool = False


class ExecutorPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding: str
    workload: str | None = None


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    name: str
    intended_use: str
    limitations: list[str] = Field(default_factory=list)
    promote: bool = False
    alias: str = "candidate"
    approvals: list[str] = Field(default_factory=list)
    vulnerability_report: str | None = None
    evaluation_ref: str | None = None


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    baseline_ref: str
    candidate_ref: str
    metric: str
    direction: str = "maximize"


class DeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: str


class DeploymentRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class ApiTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(min_length=1)
    scopes: set[str]
    expires_at: str | None = None


class GoalScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_refs: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)


class GoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    objective: str
    success_criteria: list[str]
    constraints: list[str] = Field(default_factory=list)
    budget: dict[str, float] = Field(default_factory=dict)
    priority: int = Field(default=50, ge=0, le=100)
    parent_ref: str | None = None
    scope: GoalScopeRequest = Field(default_factory=GoalScopeRequest)


class GoalStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2048)


class KnowledgeEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=1)
    digest: str | None = None


class KnowledgeScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_refs: list[str] = Field(default_factory=list)
    resource_refs: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class KnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    category: str
    claim: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[KnowledgeEvidenceRequest] = Field(min_length=1)
    scope: KnowledgeScopeRequest = Field(default_factory=KnowledgeScopeRequest)
    supersedes: list[str] = Field(default_factory=list)
    expires_at: str | None = None


def create_app(paths: ProjectPaths, *, executors: ExecutorRegistry | None = None) -> FastAPI:
    """Create one API application bound to an already bootstrapped project."""
    factory = Factory(paths, executors=executors)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            factory.close()

    app = FastAPI(
        title="Open Model Factory API",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.factory = factory

    @app.exception_handler(OMFError)
    async def omf_error(_request: Request, exc: OMFError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.as_dict())

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {
                "path": "$"
                + "".join(
                    f"[{item}]" if isinstance(item, int) else f".{item}" for item in error["loc"]
                ),
                "message": str(error["msg"]),
                "type": str(error["type"]),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_error",
                    "message": f"request failed validation ({len(errors)} error(s))",
                    "retryable": False,
                    "remediation": [
                        {
                            "action": "agent.capabilities",
                            "command": "omf agent capabilities",
                            "description": "Inspect the action contract and endpoint schema.",
                        }
                    ],
                    "details": {"errors": errors},
                }
            },
        )

    def authorized(request: Request, authorization: str = Header(default="")) -> Iterator[Factory]:
        scheme, _, token = authorization.partition(" ")
        principal = factory.authenticate_principal(token) if scheme.lower() == "bearer" else None
        if principal is None:
            raise AuthorizationError("valid Bearer authentication is required")
        admin_paths = {
            "/v1/backups",
        }
        read_post_paths = {"/v1/executors/preflight"}
        required_scope = (
            "admin"
            if request.url.path in admin_paths or request.url.path.startswith("/v1/tokens")
            else "read"
            if request.method == "GET" or request.url.path in read_post_paths
            else "write"
        )
        if not principal.allows(required_scope):
            raise AuthorizationError(f"API token lacks {required_scope} scope")
        service = Factory(paths, actor=principal.actor, executors=factory.executors)
        try:
            yield service
        finally:
            service.close()

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return {"status": "ok", "project": factory.project["metadata"]["name"]}

    @app.get("/v1/doctor")
    def doctor(service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.doctor()

    @app.get("/v1/executors")
    def executors_catalog(service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.executor_catalog()

    @app.post("/v1/executors/preflight")
    def executor_preflight(
        request: ExecutorPreflightRequest, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        return service.executor_preflight(
            paths.root / request.binding,
            workload_path=paths.root / request.workload if request.workload else None,
        )

    @app.get("/v1/agent/capabilities")
    def agent_capabilities(
        response: Response,
        if_none_match: str | None = Header(default=None),
        service: Factory = Depends(authorized),
    ) -> Any:
        value = service.agent.capabilities()
        etag = f'"{value["catalogDigest"]}"'
        if if_none_match in {etag, value["catalogDigest"]}:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "no-cache"
        return value

    @app.get("/v1/agent/context")
    def agent_context(
        response: Response,
        focus: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        since: str | None = None,
        max_bytes: int = Query(default=65_536, ge=16_384, le=1_048_576),
        if_none_match: str | None = Header(default=None),
        service: Factory = Depends(authorized),
    ) -> Any:
        value = service.agent.context(focus=focus, limit=limit, since=since, max_bytes=max_bytes)
        etag = f'"{value["viewDigest"]}"'
        if if_none_match in {etag, value["viewDigest"]}:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "no-cache"
        return value

    @app.post("/v1/goals")
    def goal_create(request: GoalRequest, service: Factory = Depends(authorized)) -> dict[str, Any]:
        scope = request.scope.model_dump()
        return service.agent.create_goal(
            request.name,
            objective=request.objective,
            success_criteria=request.success_criteria,
            constraints=request.constraints,
            budget=request.budget,
            priority=request.priority,
            parent_ref=request.parent_ref,
            scope={
                "resourceRefs": scope["resource_refs"],
                "runIds": scope["run_ids"],
            },
        )

    @app.get("/v1/goals")
    def goals(
        state: str | None = None,
        focus: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        service: Factory = Depends(authorized),
    ) -> dict[str, Any]:
        return service.agent.list_goals(state=state, focus=focus, limit=limit)

    @app.patch("/v1/goals/{name}/status")
    def goal_status(
        name: str, request: GoalStatusRequest, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        return service.agent.set_goal_status(
            name,
            state=request.state,
            expected_version=request.expected_version,
            reason=request.reason,
        )

    @app.post("/v1/knowledge")
    def knowledge_record(
        request: KnowledgeRequest, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        scope = request.scope.model_dump()
        return service.agent.record_knowledge(
            request.name,
            category=request.category,
            claim=request.claim,
            confidence=request.confidence,
            evidence=[item.model_dump(exclude_none=True) for item in request.evidence],
            scope={
                "goalRefs": scope["goal_refs"],
                "resourceRefs": scope["resource_refs"],
                "runIds": scope["run_ids"],
                "tags": scope["tags"],
            },
            supersedes=request.supersedes,
            expires_at=request.expires_at,
        )

    @app.get("/v1/knowledge")
    def knowledge(
        active_only: bool = True,
        focus: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        service: Factory = Depends(authorized),
    ) -> dict[str, Any]:
        return service.agent.list_knowledge(active_only=active_only, focus=focus, limit=limit)

    @app.get("/v1/schemas")
    def schemas(_service: Factory = Depends(authorized)) -> dict[str, Any]:
        return {"apiVersion": "omf.dev/v1alpha1", "kinds": default_registry.kinds}

    @app.get("/v1/schemas/{kind}")
    def schema(kind: str, _service: Factory = Depends(authorized)) -> dict[str, Any]:
        return default_registry.schema_for(kind)

    @app.get("/v1/resources")
    def resources(
        kind: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        service: Factory = Depends(authorized),
    ) -> list[dict[str, Any]]:
        return service.list_resources(kind=kind)[offset : offset + limit]

    @app.post("/v1/resources")
    def apply_resource(
        resource: dict[str, Any], service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        return service.apply_resource(resource)

    @app.get("/v1/events")
    def events(
        run_id: str | None = None,
        resource_uid: str | None = None,
        event_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        service: Factory = Depends(authorized),
    ) -> list[dict[str, Any]]:
        values = [
            event.as_dict()
            for event in service.events.query(
                run_id=run_id, resource_uid=resource_uid, type=event_type
            )
        ]
        return values[offset : offset + limit]

    @app.post("/v1/stores")
    def add_store(request: StoreRequest, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.add_store(
            request.name,
            driver=request.driver,
            endpoint=request.endpoint,
            secret_ref=request.secret_ref,
            plan=request.plan,
        )

    @app.post("/v1/data")
    def add_data(request: DataRequest, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.add_data(
            request.source,
            name=request.name,
            mode=request.mode,
            rights=request.rights,
            sample_schema=request.sample_schema,
            cursor_policy=request.cursor_policy,
        )

    @app.get("/v1/data/{name}/verify")
    def verify_data(name: str, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return {"name": name, "valid": service.verify_data(name)}

    @app.post("/v1/data/{name}/revoke")
    def revoke_data(
        name: str,
        request: DataRevocationRequest,
        service: Factory = Depends(authorized),
    ) -> dict[str, Any]:
        return service.revoke_data(name, reason=request.reason)

    @app.post("/v1/sync")
    def sync(request: SyncRequest, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.sync(
            request.asset,
            source=request.source,
            destination=request.destination,
            direction=request.direction,
            concurrency=request.concurrency,
            plan=request.plan,
        )

    @app.post("/v1/modules/validate")
    def validate_module(
        request: ModuleRequest, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        return service.validate_module(paths.root / request.manifest)

    @app.post("/v1/modules/test")
    def test_module(
        request: ModuleRequest, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        return service.test_module(
            paths.root / request.manifest,
            binding_path=paths.root / request.binding if request.binding else None,
        )

    def execute_run_operation(operation_id: str) -> None:
        with Factory(paths, executors=factory.executors) as reader:
            actor = str(reader.operations.get(operation_id)["request"]["actor"])
        with Factory(paths, actor=actor, executors=factory.executors) as service:
            service.execute_run_operation(operation_id)

    @app.post("/v1/runs")
    def run(
        request: RunRequest,
        background: BackgroundTasks,
        service: Factory = Depends(authorized),
    ) -> dict[str, Any]:
        if request.detach:
            operation = service.create_run_operation(
                paths.root / request.workload, paths.root / request.binding
            )
            background.add_task(execute_run_operation, operation["id"])
            return operation
        return service.run(paths.root / request.workload, paths.root / request.binding)

    @app.get("/v1/runs/{run_id}")
    def run_status(run_id: str, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.run_status(run_id)

    @app.post("/v1/evaluations")
    def evaluate(
        request: EvaluationRequest, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        return service.evaluate(request.subject)

    @app.post("/v1/releases")
    def release(request: ReleaseRequest, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.create_release(
            request.run_id,
            name=request.name,
            intended_use=request.intended_use,
            limitations=request.limitations,
            promote=request.promote,
            alias=request.alias,
            approvals=request.approvals,
            vulnerability_report=(
                paths.root / request.vulnerability_report if request.vulnerability_report else None
            ),
            evaluation_ref=request.evaluation_ref,
        )

    @app.post("/v1/experiments")
    def experiment(
        request: ExperimentRequest, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        return service.create_experiment(
            name=request.name,
            baseline_ref=request.baseline_ref,
            candidate_ref=request.candidate_ref,
            metric=request.metric,
            direction=request.direction,
        )

    @app.post("/v1/deployments")
    def deploy(
        request: DeploymentRequest, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        return service.deploy(paths.root / request.manifest)

    @app.get("/v1/deployments/{name}")
    def deployment_status(name: str, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.deployment_status(name)

    @app.post("/v1/deployments/{name}/cancel")
    def deployment_cancel(name: str, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.cancel_deployment(name)

    @app.post("/v1/deployments/{name}/rollback")
    def deployment_rollback(
        name: str,
        request: DeploymentRollbackRequest,
        service: Factory = Depends(authorized),
    ) -> dict[str, Any]:
        return service.rollback_deployment(name, expected_version=request.expected_version)

    @app.get("/v1/lineage")
    def lineage(
        subject: str,
        direction: str = "upstream",
        max_depth: int = Query(default=100, ge=1, le=1000),
        service: Factory = Depends(authorized),
    ) -> list[dict[str, Any]]:
        return service.lineage_query(subject, direction=direction, max_depth=max_depth)

    @app.post("/v1/backups")
    def backup(request: BackupRequest, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.backup(request.destination)

    @app.post("/v1/tokens")
    def token_create(
        request: ApiTokenRequest, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        token, principal = service.api_tokens.create(
            actor=request.actor, scopes=request.scopes, expires_at=request.expires_at
        )
        return {
            "token": token,
            "tokenId": principal.token_id,
            "actor": principal.actor,
            "scopes": sorted(principal.scopes),
            "expiresAt": principal.expires_at,
        }

    @app.get("/v1/tokens")
    def token_list(service: Factory = Depends(authorized)) -> list[dict[str, Any]]:
        return service.api_tokens.list()

    @app.delete("/v1/tokens/{token_id}")
    def token_revoke(token_id: str, service: Factory = Depends(authorized)) -> dict[str, Any]:
        service.revoke_api_token(token_id)
        return {"tokenId": token_id, "revoked": True}

    @app.get("/v1/operations")
    def operations(
        state: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        service: Factory = Depends(authorized),
    ) -> list[dict[str, Any]]:
        return service.operations.list(state=state)[offset : offset + limit]

    @app.get("/v1/operations/{operation_id}")
    def operation(operation_id: str, service: Factory = Depends(authorized)) -> dict[str, Any]:
        return service.operations.get(operation_id)

    @app.post("/v1/operations/{operation_id}/reconcile")
    def operation_reconcile(
        operation_id: str, service: Factory = Depends(authorized)
    ) -> dict[str, Any]:
        return service.execute_run_operation(operation_id)

    return app


def app_from_directory(project: str | Path) -> FastAPI:
    return create_app(ProjectPaths(Path(project).resolve()))
