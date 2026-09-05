from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from omf.actions import ACTION_BY_NAME, capability_catalog
from omf.canonical import canonical_json, sha256_digest
from omf.errors import CapabilityError, ConflictError, NotFoundError, ValidationError
from omf.lineage import LineageEdge

if TYPE_CHECKING:
    from omf.config import ProjectPaths
    from omf.factory import Factory

_GOAL_STATES = frozenset({"pending", "active", "blocked", "satisfied", "canceled"})


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("time must be RFC 3339 with a timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("time must include a timezone")
    return parsed.astimezone(UTC)


def _instant(value: datetime | None = None) -> datetime:
    instant = value or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValidationError("context time must include a timezone")
    return instant.astimezone(UTC)


def initial_context(
    paths: ProjectPaths,
    *,
    focus: str | None = None,
    limit: int = 20,
    since: str | None = None,
    max_bytes: int = 65_536,
) -> dict[str, Any]:
    from omf.config import bootstrap, load_project
    from omf.executors import default_executor_registry

    if since is not None:
        raise ValidationError("an event cursor cannot be used before factory bootstrap")
    AgentControl._check_limit(limit)
    if max_bytes < 16_384 or max_bytes > 1_048_576:
        raise ValidationError("max_bytes must be between 16384 and 1048576")
    project = load_project(paths)
    plan = bootstrap(paths, plan=True)
    catalog = capability_catalog()
    generated_at = _now()
    recommendation = AgentControl._recommend(
        "project.bootstrap",
        100,
        "factory_not_bootstrapped",
        "Repository-local factory state has not been initialized.",
    )
    blocker = AgentControl._blocker(
        "factory_not_bootstrapped",
        "Factory metadata, identity, secrets, and local artifact store are absent.",
        "project.bootstrap",
        [".omf"],
    )
    empty_page = {"items": [], "returned": 0, "total": 0, "truncated": False}
    context: dict[str, Any] = {
        "apiVersion": "omf.agent/v1alpha1",
        "kind": "AgentContext",
        "trust": {
            "metadata": "untrusted-data",
            "recommendations": "advisory-not-authorization",
            "budgets": "declared-not-enforced",
        },
        "generatedAt": generated_at,
        "project": {
            "name": project["metadata"]["name"],
            "namespace": project["metadata"]["namespace"],
            "revision": sha256_digest(project),
            "profile": "local",
        },
        "readiness": {
            "ready": False,
            "failures": 1,
            "checks": [
                {"name": "project-schema", "status": "pass"},
                {
                    "name": "factory-state",
                    "status": "fail",
                    "detail": "not initialized",
                    "remediation": "inspect and apply the included bootstrap plan",
                },
            ],
        },
        "bootstrapPlan": plan,
        "goals": dict(empty_page),
        "inventory": {
            "resources": [],
            "executors": default_executor_registry().catalog()["providers"],
        },
        "activity": {
            "runs": dict(empty_page),
            "deployments": dict(empty_page),
            "operations": dict(empty_page),
        },
        "knowledge": dict(empty_page),
        "recentEvents": {**empty_page, "cursor": None, "since": None},
        "blockers": {"items": [blocker], "returned": 1, "total": 1, "truncated": False},
        "recommendations": [recommendation],
        "capabilities": {
            "catalogVersion": catalog["catalogVersion"],
            "catalogDigest": catalog["catalogDigest"],
            "command": "omf agent capabilities",
            "endpoint": "/v1/agent/capabilities",
        },
        "limits": {"maxItemsPerSection": limit, "maxBytes": max_bytes, "focus": focus},
    }
    digest_input = {key: value for key, value in context.items() if key != "generatedAt"}
    result = {**context, "viewDigest": sha256_digest(digest_input)}
    if len(canonical_json(result)) > max_bytes:
        raise CapabilityError("initial agent context exceeds the requested byte budget")
    return result


class AgentControl:
    def __init__(self, factory: Factory) -> None:
        self.factory = factory

    def capabilities(self, action: str | None = None) -> dict[str, Any]:
        return capability_catalog(action)

    def create_goal(
        self,
        name: str,
        *,
        objective: str,
        success_criteria: list[str],
        constraints: list[str] | None = None,
        budget: dict[str, float] | None = None,
        priority: int = 50,
        parent_ref: str | None = None,
        scope: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "objective": objective,
            "successCriteria": success_criteria,
            "constraints": constraints or [],
            "budget": budget or {},
            "priority": priority,
            "scope": scope or {},
            "extensions": {},
        }
        if parent_ref is not None:
            spec["parentRef"] = parent_ref
        authoring = {
            "apiVersion": "omf.dev/v1alpha1",
            "kind": "Goal",
            "metadata": {"name": name, "namespace": self.factory.namespace},
            "spec": spec,
        }
        try:
            existing = self.factory.find_resource("Goal", name)
        except NotFoundError:
            resource = self.factory.apply_resource(authoring)
        else:
            if existing["specDigest"] != sha256_digest(spec):
                raise ConflictError(
                    f"goal already exists with different immutable intent: {name}",
                    details={"currentRevision": existing["metadata"]["revision"]},
                    remediation=[
                        {
                            "action": "goal.create",
                            "command": "omf goal create <new-name> ...",
                            "description": "Create a new goal or child goal for changed intent.",
                        }
                    ],
                )
            resource = existing
        uid = str(resource["metadata"]["uid"])
        try:
            status, version = self.factory.resources.get_status(uid)
        except NotFoundError:
            status = {
                "state": "active",
                "reason": "goal created",
                "updatedAt": _now(),
                "updatedBy": self.factory.actor,
            }
            version = self.factory.resources.set_status(uid, status, expected_version=None)
            self._goal_event(resource, status, version)
        return {"goal": resource, "status": status, "statusVersion": version}

    def list_goals(
        self, *, state: str | None = None, focus: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        self._check_limit(limit)
        if state is not None and state not in _GOAL_STATES:
            raise ValidationError(
                f"invalid goal state: {state}", details={"allowed": sorted(_GOAL_STATES)}
            )
        return self._page(self._goal_items(state=state, focus=focus), limit)

    def _goal_items(
        self, *, state: str | None = None, focus: str | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for resource in self.factory.resources.latest(kind="Goal"):
            try:
                status, version = self.factory.resources.get_status(resource["metadata"]["uid"])
            except NotFoundError:
                status, version = {"state": "pending", "reason": "status not initialized"}, 0
            item = {"goal": resource, "status": status, "statusVersion": version}
            if state is not None and status.get("state") != state:
                continue
            if focus and not self._matches(item, focus):
                continue
            items.append(item)
        return items

    def set_goal_status(
        self, name: str, *, state: str, expected_version: int, reason: str
    ) -> dict[str, Any]:
        self.factory._authorize("goal.status")
        if state not in _GOAL_STATES:
            raise ValidationError(
                f"invalid goal state: {state}", details={"allowed": sorted(_GOAL_STATES)}
            )
        if not reason or len(reason) > 2048:
            raise ValidationError("goal status reason must contain between 1 and 2048 characters")
        resource = self.factory.find_resource("Goal", name)
        try:
            current_status, current_version = self.factory.resources.get_status(
                resource["metadata"]["uid"]
            )
        except NotFoundError:
            current_status, current_version = {"state": "pending"}, 0
        if current_version != expected_version:
            raise ConflictError(
                "goal status version mismatch",
                details={
                    "expectedVersion": expected_version,
                    "currentVersion": current_version,
                    "currentState": current_status.get("state"),
                },
            )
        current_state = str(current_status.get("state", "pending"))
        if current_state in {"satisfied", "canceled"} and state != current_state:
            raise ValidationError(
                f"terminal goal state {current_state!r} cannot transition to {state!r}",
                remediation=[
                    {
                        "action": "goal.create",
                        "command": "omf goal create <new-name> ...",
                        "description": "Create a new goal or child goal for new intent.",
                    }
                ],
            )
        status = {
            "state": state,
            "reason": reason,
            "updatedAt": _now(),
            "updatedBy": self.factory.actor,
        }
        version = self.factory.resources.set_status(
            resource["metadata"]["uid"],
            status,
            expected_version=None if expected_version == 0 else expected_version,
        )
        self._goal_event(resource, status, version)
        return {"goal": resource, "status": status, "statusVersion": version}

    def _goal_event(self, resource: dict[str, Any], status: dict[str, Any], version: int) -> None:
        metadata = resource["metadata"]
        self.factory.events.append(
            type="GoalStatusChanged",
            source=f"omf://{self.factory.namespace}",
            subject=f"Goal/{metadata['name']}",
            resource_uid=metadata["uid"],
            revision=metadata["revision"],
            actor=self.factory.actor,
            data={"state": status["state"], "reason": status["reason"], "statusVersion": version},
            dataschema="https://schemas.omf.dev/events/goal-status/v1",
        )

    def record_knowledge(
        self,
        name: str,
        *,
        category: str,
        claim: str,
        confidence: float,
        evidence: list[dict[str, str]],
        scope: dict[str, list[str]] | None = None,
        supersedes: list[str] | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        canonical_supersedes = [self._resolve_knowledge_ref(item) for item in supersedes or []]
        spec: dict[str, Any] = {
            "category": category,
            "claim": claim,
            "confidence": confidence,
            "evidence": evidence,
            "scope": scope or {},
            "supersedes": canonical_supersedes,
            "extensions": {},
        }
        if expires_at is not None:
            _parse_time(expires_at)
            spec["expiresAt"] = expires_at
        resource = self.factory.apply_resource(
            {
                "apiVersion": "omf.dev/v1alpha1",
                "kind": "Knowledge",
                "metadata": {"name": name, "namespace": self.factory.namespace},
                "spec": spec,
            }
        )
        target = self.factory._resource_uri(resource)
        for evidence_item in evidence:
            self.factory.lineage.add(
                LineageEdge(
                    str(evidence_item["ref"]),
                    target,
                    "supports",
                    "entity",
                    "entity",
                )
            )
        for source in canonical_supersedes:
            self.factory.lineage.add(
                LineageEdge(source, target, "supersededBy", "entity", "entity")
            )
        metadata = resource["metadata"]
        self.factory.events.append(
            type="KnowledgeRecorded",
            source=f"omf://{self.factory.namespace}",
            subject=f"Knowledge/{metadata['name']}",
            resource_uid=metadata["uid"],
            revision=metadata["revision"],
            actor=self.factory.actor,
            data={
                "category": category,
                "confidence": confidence,
                "evidenceCount": len(evidence),
                "supersedes": canonical_supersedes,
            },
            dataschema="https://schemas.omf.dev/events/knowledge-recorded/v1",
            dedupe_revision=True,
        )
        return resource

    def list_knowledge(
        self,
        *,
        active_only: bool = True,
        focus: str | None = None,
        limit: int = 20,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        self._check_limit(limit)
        resources = self.factory.resources.list(kind="Knowledge")
        latest_uris = {
            self.factory._resource_uri(resource)
            for resource in self.factory.resources.latest(kind="Knowledge")
        }
        superseded = {
            str(reference)
            for resource in resources
            for reference in resource["spec"].get("supersedes", [])
        }
        instant = _instant(at)
        items: list[dict[str, Any]] = []
        for resource in resources:
            reasons: list[str] = []
            uri = self.factory._resource_uri(resource)
            if uri not in latest_uris:
                reasons.append("revised")
            if uri in superseded:
                reasons.append("superseded")
            expires_at = resource["spec"].get("expiresAt")
            if isinstance(expires_at, str) and _parse_time(expires_at) <= instant:
                reasons.append("expired")
            item = {"knowledge": resource, "active": not reasons, "inactiveReasons": reasons}
            if active_only and reasons:
                continue
            if focus and not self._matches(item, focus):
                continue
            items.append(item)
        items.sort(
            key=lambda item: (
                float(item["knowledge"]["spec"]["confidence"]),
                str(item["knowledge"]["metadata"]["createdAt"]),
            ),
            reverse=True,
        )
        return self._page(items, limit)

    def _resolve_knowledge_ref(self, reference: str) -> str:
        all_resources = self.factory.resources.list(kind="Knowledge")
        by_uri = {
            self.factory._resource_uri(item): self.factory._resource_uri(item)
            for item in all_resources
        }
        if reference in by_uri:
            return reference
        if reference.startswith("knowledge/"):
            name = reference.removeprefix("knowledge/")
            return self.factory._resource_uri(self.factory.find_resource("Knowledge", name))
        raise ValidationError(
            f"knowledge supersession target not found: {reference}",
            remediation=[
                {
                    "action": "knowledge.list",
                    "command": "omf knowledge list --all",
                    "description": "Use knowledge/<name> or an exact returned OMF URI.",
                }
            ],
        )

    def context(
        self,
        *,
        focus: str | None = None,
        limit: int = 20,
        since: str | None = None,
        max_bytes: int = 65_536,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        self._check_limit(limit)
        if max_bytes < 16_384 or max_bytes > 1_048_576:
            raise ValidationError("max_bytes must be between 16384 and 1048576")
        instant = _instant(at)
        generated_at = instant.isoformat().replace("+00:00", "Z")
        doctor = self.factory.doctor()
        readiness_checks = [
            {
                "name": item["name"],
                "status": item["status"],
                **({"detail": item["detail"]} if item["status"] == "fail" else {}),
                **({"remediation": item["remediation"]} if "remediation" in item else {}),
            }
            for item in doctor["checks"]
        ]
        readiness = {
            "ready": doctor["ready"],
            "failures": doctor["failures"],
            "checks": readiness_checks,
        }
        goals = self.list_goals(focus=focus, limit=limit)
        knowledge = self.list_knowledge(focus=focus, limit=limit, at=instant)
        event_window = self.factory.events.window(limit=limit, after=since, focus=focus)
        events = self._page(
            [
                {
                    "id": event.id,
                    "type": event.type,
                    "subject": event.subject,
                    "time": event.time,
                    "actor": event.actor,
                    "runId": event.run_id,
                    "resourceUid": event.resource_uid,
                    "revision": event.revision,
                    "payloadDigest": event.payload_digest,
                }
                for event in event_window.items
            ],
            limit,
            total=event_window.total,
            already_limited=True,
            truncated=event_window.truncated,
        )
        events.update({"cursor": event_window.cursor, "since": since})
        recent_runs = self._recent_resource_status("Run", limit, focus)
        deployments = self._recent_resource_status("DeploymentSpec", limit, focus)
        operations = self.factory.operations.summaries(limit=limit, focus=focus)
        global_goal_items = self._goal_items()
        global_goals = self._page(global_goal_items, max(len(global_goal_items), 1))
        global_runs = self._recent_resource_status("Run", 1, None)
        global_deployments = self._recent_resource_status("DeploymentSpec", 100, None)
        global_operations = self.factory.operations.summaries(states={"failed", "error"}, limit=100)
        blockers = self._blockers(
            readiness,
            global_goals,
            global_runs,
            global_deployments,
            global_operations,
            limit,
        )
        recommendations = (
            self._recommendations()
            if readiness["ready"]
            else [
                self._recommend(
                    "project.doctor",
                    100,
                    "readiness_failed",
                    "Resolve the reported readiness failure before allocating work.",
                )
            ]
        )
        catalog = self.capabilities()
        context: dict[str, Any] = {
            "apiVersion": "omf.agent/v1alpha1",
            "kind": "AgentContext",
            "trust": {
                "metadata": "untrusted-data",
                "recommendations": "advisory-not-authorization",
                "budgets": "declared-not-enforced",
            },
            "generatedAt": generated_at,
            "project": {
                "name": self.factory.project["metadata"]["name"],
                "namespace": self.factory.namespace,
                "revision": sha256_digest(self.factory.project),
                "profile": "local",
                "actor": self.factory.actor,
            },
            "readiness": readiness,
            "goals": goals,
            "inventory": {
                "resources": self.factory.resources.inventory(),
                "executors": self.factory.executor_catalog()["providers"],
            },
            "activity": {
                "runs": recent_runs,
                "deployments": deployments,
                "operations": operations,
            },
            "knowledge": knowledge,
            "recentEvents": events,
            "blockers": blockers,
            "recommendations": recommendations,
            "capabilities": {
                "catalogVersion": catalog["catalogVersion"],
                "catalogDigest": catalog["catalogDigest"],
                "command": "omf agent capabilities",
                "endpoint": "/v1/agent/capabilities",
            },
            "limits": {
                "maxItemsPerSection": limit,
                "maxBytes": max_bytes,
                "focus": focus,
            },
        }
        return self._fit_and_digest(context, max_bytes)

    def _recent_resource_status(self, kind: str, limit: int, focus: str | None) -> dict[str, Any]:
        resources = self.factory.resources.latest(kind=kind)
        items: list[dict[str, Any]] = []
        for resource in resources:
            try:
                status, version = self.factory.resources.get_status(resource["metadata"]["uid"])
            except NotFoundError:
                status, version = {"state": "unknown", "reason": "status not initialized"}, 0
            summary = {
                "name": resource["metadata"]["name"],
                "uid": resource["metadata"]["uid"],
                "revision": resource["metadata"]["revision"],
                "createdAt": resource["metadata"]["createdAt"],
                "state": status.get("state", "unknown"),
                "reason": status.get("reason"),
                "statusVersion": version,
            }
            if kind == "Run":
                summary["runId"] = resource["spec"].get("runId")
            if not focus or self._matches(summary, focus):
                items.append(summary)
        return self._page(items, limit)

    def _blockers(
        self,
        readiness: dict[str, Any],
        goals: dict[str, Any],
        runs: dict[str, Any],
        deployments: dict[str, Any],
        operations: dict[str, Any],
        limit: int,
    ) -> dict[str, Any]:
        blockers: list[dict[str, Any]] = []
        for check in readiness["checks"]:
            if check["status"] == "fail":
                blockers.append(
                    self._blocker(
                        "readiness_failed",
                        f"Readiness check {check['name']} failed: {check.get('detail', '')}",
                        "project.doctor",
                        [str(check["name"])],
                    )
                )
        for item in goals["items"]:
            if item["status"].get("state") == "blocked":
                blockers.append(
                    self._blocker(
                        "goal_blocked",
                        f"Goal {item['goal']['metadata']['name']} is blocked: "
                        f"{item['status'].get('reason', '')}",
                        "goal.status",
                        [str(item["goal"]["metadata"]["name"])],
                    )
                )
        for section, code in ((runs, "run_failed"), (deployments, "deployment_failed")):
            for item in section["items"]:
                if str(item["state"]).lower() in {"failed", "error"}:
                    blockers.append(
                        self._blocker(
                            code,
                            f"{item['name']} is {item['state']}: "
                            f"{item.get('reason') or 'no reason'}",
                            "run.status" if code == "run_failed" else "deployment.status",
                            [str(item["name"])],
                        )
                    )
        for item in operations["items"]:
            if str(item["state"]).lower() in {"failed", "error"}:
                blockers.append(
                    self._blocker(
                        "operation_failed",
                        f"Operation {item['id']} failed.",
                        "agent.context",
                        [str(item["id"])],
                    )
                )
        ordered = sorted(blockers, key=lambda item: (item["severity"], item["id"]))
        return self._page(ordered, limit)

    def _run_recommendations(self, latest_run: dict[str, Any]) -> list[dict[str, Any]]:
        run_id = str(latest_run.get("runId"))
        state = str(latest_run.get("state", "")).lower()
        if state in {"failed", "error"}:
            return [
                self._recommend(
                    "run.status",
                    90,
                    "run_failed",
                    "Inspect exact stage state and admitted digests before changing the workload.",
                    command=f"omf runs status {run_id}",
                    parameters={"runId": run_id},
                )
            ]
        if state != "succeeded":
            return []
        evaluations = [
            item
            for item in self.factory.resources.latest(kind="EvaluationResult")
            if item["spec"].get("extensions", {}).get("runId") == run_id
        ]
        if not evaluations:
            return [
                self._recommend(
                    "evaluation.create",
                    80,
                    "evaluation_missing",
                    "The latest succeeded run has no immutable evaluation evidence.",
                    command=f"omf evaluate run/{run_id}",
                    parameters={"runId": run_id},
                )
            ]
        if not bool(evaluations[0]["spec"].get("extensions", {}).get("passed")):
            return []
        releases = [
            item
            for item in self.factory.resources.latest(kind="Release")
            if item["spec"]
            .get("extensions", {})
            .get("manifest", {})
            .get("provenance", {})
            .get("runId")
            == run_id
        ]
        if releases:
            return []
        return [
            self._recommend(
                "release.create",
                55,
                "release_missing",
                "A passing evaluated run is not yet packaged as a signed release.",
                parameters={"runId": run_id},
            )
        ]

    def _recommendations(self) -> list[dict[str, Any]]:
        runs = self._recent_resource_status("Run", 1, None)["items"]
        return self._run_recommendations(runs[0]) if runs else []

    @staticmethod
    def _recommend(
        action: str,
        priority: int,
        reason_code: str,
        reason: str,
        *,
        command: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        definition = ACTION_BY_NAME[action]
        identity = {
            "action": action,
            "reasonCode": reason_code,
            "parameters": parameters or {},
        }
        return {
            "id": sha256_digest(identity),
            "action": action,
            "priority": priority,
            "reasonCode": reason_code,
            "reason": reason,
            "command": command or definition.command,
            "parameters": parameters or {},
            "preconditions": list(definition.preconditions),
            "expectedEffects": list(definition.effects),
            "estimatedCost": {
                "class": definition.cost_class,
                "basis": "Exact consumption depends on the selected data, module, and binding.",
            },
            "risk": definition.risk,
            "approvalRequired": definition.approval_required,
            "destructive": definition.destructive,
            "planSupported": definition.plan_supported,
            "idempotency": definition.idempotency,
        }

    @staticmethod
    def _blocker(code: str, message: str, action: str, refs: list[str]) -> dict[str, Any]:
        bounded_message = message if len(message) <= 512 else message[:509] + "..."
        bounded_refs = [item if len(item) <= 256 else item[:253] + "..." for item in refs[:16]]
        identity = {"code": code, "message": bounded_message, "refs": bounded_refs}
        return {
            "id": sha256_digest(identity),
            "severity": "error",
            "code": code,
            "message": bounded_message,
            "relatedRefs": bounded_refs,
            "remediationAction": action,
        }

    @staticmethod
    def _page(
        items: list[dict[str, Any]],
        limit: int,
        *,
        total: int | None = None,
        already_limited: bool = False,
        truncated: bool = False,
    ) -> dict[str, Any]:
        selected = items if already_limited else items[:limit]
        count = len(items) if total is None else total
        return {
            "items": selected,
            "returned": len(selected),
            "total": count,
            "truncated": truncated or count > len(selected),
        }

    @staticmethod
    def _matches(value: Any, focus: str) -> bool:
        return focus.casefold() in canonical_json(value).decode().casefold()

    @staticmethod
    def _check_limit(limit: int) -> None:
        if limit < 1 or limit > 100:
            raise ValidationError("limit must be between 1 and 100")

    def _fit_and_digest(self, context: dict[str, Any], max_bytes: int) -> dict[str, Any]:
        while True:
            digest_input = {key: value for key, value in context.items() if key != "generatedAt"}
            candidate = {**context, "viewDigest": sha256_digest(digest_input)}
            if len(canonical_json(candidate)) <= max_bytes:
                return candidate
            if not self._trim_context(context):
                raise CapabilityError(
                    "agent context base exceeds the requested byte budget",
                    details={"maxBytes": max_bytes},
                    remediation=[
                        {
                            "action": "agent.context",
                            "command": "omf agent context --max-bytes 65536",
                            "description": (
                                "Increase the context budget or select a narrower focus."
                            ),
                        }
                    ],
                )

    @staticmethod
    def _trim_context(context: dict[str, Any]) -> bool:
        pages = [
            context["recentEvents"],
            context["knowledge"],
            context["activity"]["operations"],
            context["activity"]["deployments"],
            context["activity"]["runs"],
            context["goals"],
        ]
        for page in pages:
            if page is context["recentEvents"] and len(page["items"]) == 1:
                continue
            if page["items"]:
                page["items"].pop()
                page["returned"] = len(page["items"])
                page["truncated"] = True
                if page is context["recentEvents"]:
                    items = page["items"]
                    if page["since"] is not None:
                        page["cursor"] = items[-1]["id"] if items else page["since"]
                    else:
                        page["cursor"] = items[0]["id"] if items else None
                return True
        blockers = context["blockers"]
        if len(blockers["items"]) > 1:
            blockers["items"].pop()
            blockers["returned"] = len(blockers["items"])
            blockers["truncated"] = True
            return True
        if len(context["recommendations"]) > 1:
            context["recommendations"].pop()
            return True
        if context["inventory"]["resources"]:
            context["inventory"]["resources"].pop()
            context["limits"]["inventoryTruncated"] = True
            return True
        if context["inventory"]["executors"]:
            context["inventory"]["executors"].pop()
            context["limits"]["executorInventoryTruncated"] = True
            return True
        return False
