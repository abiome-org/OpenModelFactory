"""Bounded, deterministic control and accumulated-knowledge surface for agents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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


@dataclass(frozen=True)
class ActionDefinition:
    """Stable machine contract for one agent-operable action."""

    action: str
    description: str
    command: str
    method: str | None
    path: str | None
    scope: str
    mutates: bool
    plan_supported: bool
    idempotency: str
    risk: str
    cost_class: str
    preconditions: tuple[str, ...]
    effects: tuple[str, ...]
    approval_required: bool = False
    destructive: bool = False

    def as_dict(self) -> dict[str, Any]:
        interfaces: dict[str, Any] = {"cli": self.command}
        if self.method is not None and self.path is not None:
            interfaces["http"] = {"method": self.method, "path": self.path}
        return {
            "action": self.action,
            "description": self.description,
            "interfaces": interfaces,
            "requiredScope": self.scope,
            "mutates": self.mutates,
            "planSupported": self.plan_supported,
            "idempotency": self.idempotency,
            "risk": self.risk,
            "costClass": self.cost_class,
            "preconditions": list(self.preconditions),
            "effects": list(self.effects),
            "approvalRequired": self.approval_required,
            "destructive": self.destructive,
        }


_ACTIONS: tuple[ActionDefinition, ...] = (
    ActionDefinition(
        "project.bootstrap",
        "Plan and initialize repository-scoped local factory state.",
        "omf bootstrap --plan && omf bootstrap",
        None,
        None,
        "admin",
        True,
        True,
        "content-idempotent",
        "medium",
        "io",
        ("project.manifest-valid",),
        ("Local database, identity, secrets, and artifact store initialized.",),
        approval_required=True,
    ),
    ActionDefinition(
        "agent.context",
        "Read a bounded decision context and incremental event cursor.",
        "omf agent context",
        "GET",
        "/v1/agent/context",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("project.manifest-valid",),
        ("No state change.",),
    ),
    ActionDefinition(
        "agent.capabilities",
        "Discover action contracts, effects, risk, and cost classes.",
        "omf agent capabilities",
        "GET",
        "/v1/agent/capabilities",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        (),
        ("No state change.",),
    ),
    ActionDefinition(
        "project.doctor",
        "Run non-mutating repository and factory readiness checks.",
        "omf doctor",
        "GET",
        "/v1/doctor",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("factory.bootstrapped",),
        ("No state change.",),
    ),
    ActionDefinition(
        "executor.list",
        "Discover built-in and trusted plugin executor providers and configuration contracts.",
        "omf executor list",
        "GET",
        "/v1/executors",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("factory.bootstrapped",),
        ("No state change.",),
    ),
    ActionDefinition(
        "executor.preflight",
        "Check a binding provider and workload transport contract without allocating a run.",
        "omf executor preflight <binding> [--workload <workload>]",
        "POST",
        "/v1/executors/preflight",
        "read",
        False,
        False,
        "read-only",
        "low",
        "io",
        ("binding.valid", "provider.installed"),
        ("No run, event, or resource is allocated.",),
    ),
    ActionDefinition(
        "goal.create",
        "Persist an objective, measurable success criteria, constraints, and budget.",
        "omf goal create <name> --objective <text> --success <criterion>",
        "POST",
        "/v1/goals",
        "write",
        True,
        False,
        "content-idempotent",
        "low",
        "metadata",
        ("factory.ready",),
        ("Goal revision committed.", "Goal status initialized.", "Signed events emitted."),
    ),
    ActionDefinition(
        "goal.status",
        "Guard a goal lifecycle transition with its observed status version.",
        "omf goal status <name> --state <state> --expected-version <version>",
        "PATCH",
        "/v1/goals/{name}/status",
        "write",
        True,
        False,
        "guarded-compare-and-set",
        "low",
        "metadata",
        ("goal.exists", "expectedVersion.current"),
        ("Goal status advanced.", "Signed status event emitted."),
    ),
    ActionDefinition(
        "goal.list",
        "Read bounded current goal revisions and guarded statuses.",
        "omf goal list",
        "GET",
        "/v1/goals",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("factory.bootstrapped",),
        ("No state change.",),
    ),
    ActionDefinition(
        "knowledge.record",
        "Record an evidence-backed claim, decision, constraint, or lesson.",
        "omf knowledge record <name> --category <category> --claim <text> --evidence <ref>",
        "POST",
        "/v1/knowledge",
        "write",
        True,
        False,
        "content-idempotent",
        "low",
        "metadata",
        ("factory.ready", "evidence.nonempty"),
        ("Knowledge revision committed.", "Evidence lineage linked.", "Signed event emitted."),
    ),
    ActionDefinition(
        "knowledge.list",
        "Read active or historical evidence-backed knowledge revisions.",
        "omf knowledge list",
        "GET",
        "/v1/knowledge",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("factory.bootstrapped",),
        ("No state change.",),
    ),
    ActionDefinition(
        "resource.apply",
        "Validate and commit an immutable resource revision.",
        "omf resource apply <manifest>",
        "POST",
        "/v1/resources",
        "write",
        True,
        False,
        "content-idempotent",
        "low",
        "metadata",
        ("factory.ready", "resource.schema-valid", "resource.namespace-matches"),
        ("Immutable resource revision committed.", "SpecValidated event emitted."),
    ),
    ActionDefinition(
        "module.validate",
        "Package and validate an exact module source revision and contract.",
        "omf module validate [manifest]",
        "POST",
        "/v1/modules/validate",
        "write",
        True,
        False,
        "content-idempotent",
        "low",
        "io",
        ("module.manifest-exists",),
        ("Module source artifact committed.", "Contract result returned."),
    ),
    ActionDefinition(
        "module.test",
        "Execute module compatibility fixtures in the local isolation boundary.",
        "omf module test [manifest]",
        "POST",
        "/v1/modules/test",
        "write",
        True,
        False,
        "not-guaranteed",
        "medium",
        "compute",
        ("module.contract-valid",),
        ("Fixture executions performed.", "Contract evidence returned."),
    ),
    ActionDefinition(
        "data.add",
        "Create an immutable rights-declared dataset snapshot.",
        "omf data add <source> --name <name> --rights <manifest>",
        "POST",
        "/v1/data",
        "write",
        True,
        False,
        "resource-content-idempotent-events-repeat",
        "medium",
        "io",
        ("source.readable", "rights.declared"),
        (
            "DatasetSnapshot committed.",
            "Payload imported when copy mode is selected.",
        ),
    ),
    ActionDefinition(
        "store.add",
        "Declare a user-selected artifact holding site by symbolic secret reference.",
        "omf store add <name> --driver <driver> --endpoint <endpoint> [--plan]",
        "POST",
        "/v1/stores",
        "write",
        True,
        True,
        "content-idempotent",
        "medium",
        "metadata",
        ("driver.supported",),
        ("ArtifactStore revision committed when not planning.",),
    ),
    ActionDefinition(
        "sync.execute",
        "Plan or transfer only missing verified artifact chunks.",
        "omf sync <push|pull> <asset> <store-options> [--plan]",
        "POST",
        "/v1/sync",
        "write",
        True,
        True,
        "transfer-convergent-events-repeat",
        "medium",
        "io",
        ("asset.exists", "stores.reachable", "transfer.policy-allows"),
        ("Missing chunks transferred when not planning.", "Replica event emitted."),
    ),
    ActionDefinition(
        "workload.run",
        "Admit and execute a model-neutral workload through an explicit binding.",
        "omf run <workload> --binding <binding>",
        "POST",
        "/v1/runs",
        "write",
        True,
        False,
        "new-run-identity",
        "high",
        "compute",
        ("factory.ready", "workload.valid", "binding.supported", "inputs.available"),
        ("Unique Run committed.", "Stages executed.", "Outputs, events, and lineage recorded."),
        approval_required=True,
    ),
    ActionDefinition(
        "run.status",
        "Read observed run state and exact admitted execution digests.",
        "omf runs status <run-id>",
        "GET",
        "/v1/runs/{run_id}",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("run.exists",),
        ("No state change.",),
    ),
    ActionDefinition(
        "evaluation.create",
        "Materialize immutable evaluation evidence for a completed run.",
        "omf evaluate run/<run-id>",
        "POST",
        "/v1/evaluations",
        "write",
        True,
        False,
        "resource-content-idempotent-events-repeat",
        "medium",
        "compute",
        ("run.succeeded", "evaluation.outputs-present"),
        ("EvaluationResult committed.", "Evaluation event and lineage emitted."),
    ),
    ActionDefinition(
        "experiment.create",
        "Compare one numeric metric under identical immutable evaluation revisions.",
        "omf experiment create <name> --baseline <ref> --candidate <ref> --metric <name>",
        "POST",
        "/v1/experiments",
        "write",
        True,
        False,
        "content-idempotent",
        "low",
        "metadata",
        ("evaluation.baseline-exists", "evaluation.candidate-exists", "protocol.same"),
        ("Immutable Experiment decision committed.",),
    ),
    ActionDefinition(
        "release.create",
        "Build and optionally promote a signed complete release through fail-closed gates.",
        "omf release create <run-id> --name <name> --intended-use <use>",
        "POST",
        "/v1/releases",
        "write",
        True,
        False,
        "not-guaranteed",
        "high",
        "external",
        ("run.succeeded", "evaluation.passed", "promotion.evidence-complete"),
        ("Signed Release committed.", "Alias may move only when promotion gates allow."),
        approval_required=True,
    ),
    ActionDefinition(
        "deployment.apply",
        "Policy-check and apply an explicit deployment revision.",
        "omf deploy <manifest>",
        "POST",
        "/v1/deployments",
        "write",
        True,
        False,
        "not-guaranteed",
        "high",
        "compute",
        ("release.signed", "promotion.allowed", "deployment.valid"),
        ("Deployment status changed.", "Execution may be started.", "Lineage emitted."),
        approval_required=True,
    ),
    ActionDefinition(
        "deployment.status",
        "Reconcile and read observed deployment state.",
        "omf deployment status <name>",
        "GET",
        "/v1/deployments/{name}",
        "read",
        True,
        False,
        "convergent-reconciliation",
        "low",
        "negligible",
        ("deployment.exists",),
        ("Terminal worker state may be reconciled into status.",),
    ),
    ActionDefinition(
        "deployment.rollback",
        "Guard and restore the previous immutable deployment revision.",
        "omf deployment rollback <name> --expected-version <version>",
        "POST",
        "/v1/deployments/{name}/rollback",
        "write",
        True,
        False,
        "guarded-compare-and-set",
        "high",
        "compute",
        ("deployment.previous-revision-exists", "expectedVersion.current"),
        ("Current execution may stop.", "Previous deployment revision starts."),
        approval_required=True,
        destructive=True,
    ),
    ActionDefinition(
        "lineage.query",
        "Trace upstream derivation or downstream impact.",
        "omf lineage show <subject> --direction <upstream|downstream>",
        "GET",
        "/v1/lineage",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("subject.identified",),
        ("No state change.",),
    ),
    ActionDefinition(
        "backup.create",
        "Create a consistent local metadata backup and verify its integrity.",
        "omf backup <destination>",
        "POST",
        "/v1/backups",
        "admin",
        True,
        False,
        "not-guaranteed",
        "medium",
        "io",
        ("destination.writable",),
        ("Database backup written and integrity-checked.",),
        approval_required=True,
    ),
)

_ACTIONS += (
    ActionDefinition(
        "schema.list",
        "List installed resource kinds and schema contracts.",
        "omf schema list",
        "GET",
        "/v1/schemas",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        (),
        ("No state change.",),
    ),
    ActionDefinition(
        "schema.show",
        "Read the exact JSON Schema for one resource kind.",
        "omf schema show <kind>",
        "GET",
        "/v1/schemas/{kind}",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("schema.kind-installed",),
        ("No state change.",),
    ),
    ActionDefinition(
        "schema.validate",
        "Validate a local desired-state document without committing it.",
        "omf schema validate <manifest>",
        None,
        None,
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("manifest.readable",),
        ("No state change.",),
    ),
    ActionDefinition(
        "resource.list",
        "Read immutable resource revisions with an optional kind filter.",
        "omf resource list [--kind <kind>]",
        "GET",
        "/v1/resources",
        "read",
        False,
        False,
        "read-only",
        "low",
        "metadata",
        ("factory.bootstrapped",),
        ("No state change.",),
    ),
    ActionDefinition(
        "event.list",
        "Read signed event records, including governed event payloads.",
        "omf --output json event list [--run-id <run-id>]",
        "GET",
        "/v1/events",
        "read",
        False,
        False,
        "read-only",
        "medium",
        "metadata",
        ("factory.bootstrapped", "caller.authorized-for-event-payloads"),
        ("No state change; response may contain sensitive event payloads.",),
    ),
    ActionDefinition(
        "data.verify",
        "Verify a dataset snapshot and locally held content by digest.",
        "omf data verify <name>",
        "GET",
        "/v1/data/{name}/verify",
        "read",
        False,
        False,
        "read-only",
        "low",
        "io",
        ("dataset.exists", "referenced-content.available"),
        ("No state change.",),
    ),
    ActionDefinition(
        "deployment.cancel",
        "Stop a deployment and record its terminal status.",
        "omf deployment cancel <name>",
        "POST",
        "/v1/deployments/{name}/cancel",
        "write",
        True,
        False,
        "convergent-reconciliation",
        "high",
        "compute",
        ("deployment.exists",),
        ("Current execution stops.", "Deployment status and event advance."),
        approval_required=True,
        destructive=True,
    ),
    ActionDefinition(
        "token.create",
        "Create an attributable scoped API credential returned exactly once.",
        "omf token create --actor <actor> --scope <scope>",
        "POST",
        "/v1/tokens",
        "admin",
        True,
        False,
        "new-credential-identity",
        "high",
        "metadata",
        ("actor.identified", "scopes.valid"),
        ("API credential created; plaintext token returned once.",),
        approval_required=True,
    ),
    ActionDefinition(
        "token.list",
        "List API credential metadata without plaintext token values.",
        "omf token list",
        "GET",
        "/v1/tokens",
        "admin",
        False,
        False,
        "read-only",
        "medium",
        "metadata",
        ("factory.bootstrapped",),
        ("No state change.",),
    ),
    ActionDefinition(
        "token.revoke",
        "Irreversibly revoke one API credential by stable token ID.",
        "omf token revoke <token-id>",
        "DELETE",
        "/v1/tokens/{token_id}",
        "admin",
        True,
        False,
        "content-idempotent",
        "high",
        "metadata",
        ("token.exists",),
        ("Credential can no longer authenticate.",),
        approval_required=True,
        destructive=True,
    ),
    ActionDefinition(
        "operation.list",
        "Read operation lifecycle metadata with an optional state filter.",
        "omf operation list [--state <state>]",
        "GET",
        "/v1/operations",
        "read",
        False,
        False,
        "read-only",
        "low",
        "metadata",
        ("factory.bootstrapped",),
        ("No state change; full operation records may include request or result data.",),
    ),
    ActionDefinition(
        "operation.get",
        "Read one exact operation lifecycle record.",
        "omf operation get <operation-id>",
        "GET",
        "/v1/operations/{operation_id}",
        "read",
        False,
        False,
        "read-only",
        "medium",
        "metadata",
        ("operation.exists", "caller.authorized-for-operation-payloads"),
        ("No state change; response may include request or result data.",),
    ),
    ActionDefinition(
        "operation.reconcile",
        "Execute a pending run or safely reconcile a stale running operation.",
        "omf operation reconcile <operation-id>",
        "POST",
        "/v1/operations/{operation_id}/reconcile",
        "write",
        True,
        False,
        "operation-keyed-no-replay",
        "high",
        "compute",
        ("operation.exists", "operation.kind=run", "operation.actor=caller"),
        (
            (
                "Pending work executes once under an exclusive lease; stale work reconciles from "
                "an immutable result or fails indeterminate without replay."
            ),
        ),
    ),
    ActionDefinition(
        "federation.identity",
        "Export this factory's public trust bundle.",
        "omf federation identity",
        "GET",
        "/v1/federation/identity",
        "read",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("factory.bootstrapped",),
        ("No state change; no private signing material is returned.",),
    ),
    ActionDefinition(
        "federation.trust",
        "Trust or replace a peer's public federation identity.",
        "omf federation trust <peer-id> <bundle>",
        "POST",
        "/v1/federation/trust",
        "admin",
        True,
        False,
        "named-peer-upsert",
        "high",
        "metadata",
        ("peer.id-verified", "trust-bundle.valid"),
        ("Future peer signatures are evaluated against the supplied identity.",),
        approval_required=True,
    ),
    ActionDefinition(
        "federation.lease",
        "Issue a bounded policy-epoch lease to a trusted peer.",
        "omf federation lease <peer-id> --lease-id <id> --expires-at <time>",
        "POST",
        "/v1/federation/leases",
        "admin",
        True,
        False,
        "lease-identity-guarded",
        "high",
        "external",
        ("peer.trusted", "expiry.future", "policyEpoch.current"),
        ("Peer receives bounded authority until expiry or epoch invalidation.",),
        approval_required=True,
    ),
    ActionDefinition(
        "federation.emit",
        "Sign and queue a federated event for a leased peer.",
        "omf federation emit <peer-id> --content <path> --lease-id <id> --kind <kind> "
        "--resource <ref>",
        "POST",
        "/v1/federation/events",
        "write",
        True,
        False,
        "new-event-identity",
        "medium",
        "external",
        ("peer.trusted", "lease.valid", "content.canonical"),
        ("Signed event queued in the peer outbox.",),
    ),
    ActionDefinition(
        "federation.reconcile",
        "Verify and idempotently reconcile an incoming federated event.",
        "omf federation reconcile <event>",
        "POST",
        "/v1/federation/reconcile",
        "write",
        True,
        False,
        "event-idempotent",
        "medium",
        "metadata",
        ("peer.trusted", "lease.valid", "signature.valid", "sequence.acceptable"),
        ("Accepted event stored once; replay does not duplicate state.",),
    ),
    ActionDefinition(
        "federation.outbox",
        "List pending signed events for one or all federation peers.",
        "omf federation outbox [--peer-id <peer-id>]",
        "GET",
        "/v1/federation/outbox",
        "read",
        False,
        False,
        "read-only",
        "medium",
        "metadata",
        ("factory.bootstrapped",),
        ("No state change; response contains federated event content.",),
    ),
    ActionDefinition(
        "federation.published",
        "Idempotently acknowledge successful peer delivery for an outbox event.",
        "omf federation published <peer-id> <event-id>",
        "POST",
        "/v1/federation/outbox/published",
        "write",
        True,
        False,
        "content-idempotent",
        "medium",
        "metadata",
        ("outbox-event.exists", "peer-delivery.verified"),
        ("Event leaves the pending delivery view; immutable history remains.",),
    ),
    ActionDefinition(
        "capacity.place",
        "Select a policy-compatible capacity offer without allocating it.",
        "omf capacity place <offers> --residency <label> --resource <type>",
        "POST",
        "/v1/capacity/place",
        "write",
        False,
        False,
        "read-only",
        "low",
        "negligible",
        ("offers.current", "constraints.explicit"),
        ("No allocation or state change; selected offer is returned.",),
    ),
    ActionDefinition(
        "secret.set",
        "Encrypt and create or replace a purpose-bound local secret.",
        "omf secret set <name> --purpose <purpose> --value <value> [--expected-version <version>]",
        None,
        None,
        "admin",
        True,
        False,
        "guarded-compare-and-set",
        "high",
        "metadata",
        ("operator.approved", "purpose.explicit"),
        ("Encrypted secret version advances; plaintext is not logged or listed.",),
        approval_required=True,
        destructive=True,
    ),
    ActionDefinition(
        "secret.list",
        "List secret names, purposes, and versions without plaintext values.",
        "omf secret list",
        None,
        None,
        "admin",
        False,
        False,
        "read-only",
        "medium",
        "metadata",
        ("factory.bootstrapped",),
        ("No state change; no plaintext values are returned.",),
    ),
)

_ACTION_BY_NAME = {item.action: item for item in _ACTIONS}


def capability_catalog() -> dict[str, Any]:
    """Return the action catalog without requiring initialized factory state."""
    actions = [item.as_dict() for item in _ACTIONS]
    body = {"apiVersion": "omf.agent/v1alpha1", "catalogVersion": 1, "actions": actions}
    return {**body, "catalogDigest": sha256_digest(body)}


def initial_context(
    paths: ProjectPaths,
    *,
    focus: str | None = None,
    limit: int = 20,
    since: str | None = None,
    max_bytes: int = 65_536,
) -> dict[str, Any]:
    """Return an actionable context before repository-local state exists."""
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
    """Agent-facing projection over the authoritative factory state."""

    def __init__(self, factory: Factory) -> None:
        self.factory = factory

    def capabilities(self) -> dict[str, Any]:
        return capability_catalog()

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
        """Build a bounded projection of facts needed for the next control decision."""
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
        operations_all = self.factory.operations.recent(limit=limit + 1)
        operations = self._page(
            [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "state": item["state"],
                    "createdAt": item["createdAt"],
                    "updatedAt": item["updatedAt"],
                    "version": item["version"],
                    "hasError": item.get("error") is not None,
                }
                for item in operations_all
                if not focus or self._matches(item, focus)
            ],
            limit,
        )
        global_goal_items = self._goal_items()
        global_goals = self._page(global_goal_items, max(len(global_goal_items), 1))
        global_runs = self._recent_resource_status("Run", 1, None)
        global_deployments = self._recent_resource_status("DeploymentSpec", 100, None)
        failed_operations = self.factory.operations.recent(states={"failed", "error"}, limit=100)
        global_operations = self._page(
            [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "state": item["state"],
                    "createdAt": item["createdAt"],
                    "updatedAt": item["updatedAt"],
                    "version": item["version"],
                    "hasError": item.get("error") is not None,
                }
                for item in failed_operations
            ],
            100,
        )
        blockers = self._blockers(
            readiness,
            global_goals,
            global_runs,
            global_deployments,
            global_operations,
            limit,
        )
        recommendations = self._recommendations(instant)
        catalog = self.capabilities()
        context: dict[str, Any] = {
            "apiVersion": "omf.agent/v1alpha1",
            "kind": "AgentContext",
            "generatedAt": generated_at,
            "project": {
                "name": self.factory.project["metadata"]["name"],
                "namespace": self.factory.namespace,
                "revision": sha256_digest(self.factory.project),
                "profile": "local",
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

    def _recommendations(self, at: datetime | None = None) -> list[dict[str, Any]]:
        recommendations: list[dict[str, Any]] = []
        active_goals = [
            item for item in self._goal_items() if item["status"].get("state") == "active"
        ]
        if not active_goals:
            recommendations.append(
                self._recommend(
                    "goal.create",
                    100,
                    "intent_missing",
                    "No active goal defines success, constraints, or budget.",
                )
            )
        inventory = {item["kind"]: item["objects"] for item in self.factory.resources.inventory()}
        if not inventory.get("DatasetSnapshot"):
            recommendations.append(
                self._recommend(
                    "data.add",
                    80,
                    "dataset_missing",
                    "No immutable dataset snapshot is available to a workload.",
                )
            )
        run_items = self._recent_resource_status("Run", 1, None)["items"]
        if not run_items:
            recommendations.extend(
                [
                    self._recommend(
                        "module.validate",
                        70,
                        "module_admission_needed",
                        "Validate exact module source and contracts before allocating compute.",
                    ),
                    self._recommend(
                        "module.test",
                        65,
                        "module_evidence_needed",
                        "Exercise module fixtures before a workload run.",
                    ),
                    self._recommend(
                        "workload.run",
                        60,
                        "run_missing",
                        "No workload run has been recorded.",
                    ),
                ]
            )
        else:
            latest_run = run_items[0]
            run_id = str(latest_run.get("runId"))
            state = str(latest_run.get("state", "")).lower()
            if state in {"failed", "error"}:
                recommendations.append(
                    self._recommend(
                        "run.status",
                        90,
                        "run_failed",
                        "Inspect exact stage state and admitted digests before changing "
                        "the workload.",
                        command=f"omf runs status {run_id}",
                        parameters={"runId": run_id},
                    )
                )
            elif state == "succeeded":
                evaluations = [
                    item
                    for item in self.factory.resources.latest(kind="EvaluationResult")
                    if item["spec"].get("extensions", {}).get("runId") == run_id
                ]
                if not evaluations:
                    recommendations.append(
                        self._recommend(
                            "evaluation.create",
                            80,
                            "evaluation_missing",
                            "The latest succeeded run has no immutable evaluation evidence.",
                            command=f"omf evaluate run/{run_id}",
                            parameters={"runId": run_id},
                        )
                    )
                elif bool(evaluations[0]["spec"].get("extensions", {}).get("passed")):
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
                    if not releases:
                        recommendations.append(
                            self._recommend(
                                "release.create",
                                55,
                                "release_missing",
                                "A passing evaluated run is not yet packaged as a signed release.",
                                parameters={"runId": run_id},
                            )
                        )
        deployments = self._recent_resource_status("DeploymentSpec", 1, None)
        if inventory.get("Release") and not deployments["items"]:
            recommendations.append(
                self._recommend(
                    "deployment.apply",
                    40,
                    "deployment_missing",
                    "A release exists but no deployment has been applied.",
                )
            )
        if not self.list_knowledge(limit=1, at=at)["items"]:
            recommendations.append(
                self._recommend(
                    "knowledge.record",
                    20,
                    "knowledge_missing",
                    "No active evidence-backed project knowledge has been retained.",
                )
            )
        return sorted(recommendations, key=lambda item: (-int(item["priority"]), item["action"]))

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
        definition = _ACTION_BY_NAME[action]
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
            "idempotencyKey": sha256_digest({**identity, "contract": 1}),
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
            if page["items"]:
                page["items"].pop()
                page["returned"] = len(page["items"])
                page["truncated"] = True
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
