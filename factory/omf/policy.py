from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from omf.canonical import load_document, sha256_digest
from omf.errors import IntegrityError, ValidationError
from omf.security import verify

WORKTREE_MODES = ("deny", "allow", "archive")
_POLICY_SUFFIXES = (".yaml", ".yml", ".json")


@dataclass(frozen=True)
class PolicyRule:
    name: str
    effect: Literal["allow", "deny", "warn"]
    match: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class PolicyDecision:
    outcome: Literal["allow", "deny", "warn"]
    policy_digest: str
    explanations: tuple[dict[str, str], ...]


class PolicyEngine:
    fields = frozenset(
        {"actor", "action", "resource", "purpose", "classification", "residency", "evidence"}
    )

    def __init__(self, rules: list[PolicyRule]) -> None:
        self.rules = tuple(rules)
        self.digest = sha256_digest(
            [
                {"name": r.name, "effect": r.effect, "match": r.match, "reason": r.reason}
                for r in self.rules
            ]
        )

    def evaluate(self, context: dict[str, Any]) -> PolicyDecision:
        matched: list[dict[str, str]] = []
        effects: list[str] = []
        for rule in self.rules:
            if all(
                self._matches(context.get(key), expected)
                for key, expected in rule.match.items()
                if key in self.fields
            ):
                effects.append(rule.effect)
                matched.append({"rule": rule.name, "effect": rule.effect, "reason": rule.reason})
        outcome: Literal["allow", "deny", "warn"] = "deny"
        if "deny" in effects:
            outcome = "deny"
        elif "warn" in effects:
            outcome = "warn"
        elif "allow" in effects:
            outcome = "allow"
        if not matched:
            matched.append({"rule": "default-deny", "effect": "deny", "reason": "no rule matched"})
        return PolicyDecision(outcome, self.digest, tuple(matched))

    @staticmethod
    def _matches(actual: Any, expected: Any) -> bool:
        if expected == "*":
            return actual is not None
        if isinstance(expected, list):
            return actual in expected or (
                isinstance(actual, (list, set, tuple)) and bool(set(actual) & set(expected))
            )
        return bool(actual == expected)


def _dirty_worktree(value: Any) -> Any:
    if value not in WORKTREE_MODES:
        raise ValidationError(f"policy dirtyWorktree must be one of {', '.join(WORKTREE_MODES)}")
    return value


def _unsigned_modules(value: Any) -> Any:
    if value != "deny":
        raise ValidationError("policy unsignedModules supports only 'deny'")
    return value


def _sync(value: Any) -> Any:
    if not isinstance(value, dict) or set(value) - {"requirePlan", "allowDelete"}:
        raise ValidationError("policy sync accepts only requirePlan and allowDelete")
    if not isinstance(value.get("requirePlan", True), bool):
        raise ValidationError("policy sync.requirePlan must be a boolean")
    if value.get("allowDelete", False) is not False:
        raise ValidationError("policy sync.allowDelete must be false; sync never deletes")
    return dict(value)


def _promotion(value: Any) -> Any:
    allowed = {"requireEvaluationPass", "requireCompleteLineage"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValidationError(f"policy promotion accepts only {', '.join(sorted(allowed))}")
    if any(value.get(name, True) is not True for name in allowed):
        raise ValidationError("policy promotion gates are mandatory and must remain true")
    return dict(value)


_CONFIG_KEYS: dict[str, Callable[[Any], Any]] = {
    "dirtyWorktree": _dirty_worktree,
    "unsignedModules": _unsigned_modules,
    "sync": _sync,
    "promotion": _promotion,
}


def _validate_policy_config(config: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for key, value in config.items():
        if key not in _CONFIG_KEYS:
            raise ValidationError(f"policy config key is not enforced by this factory: {key}")
        validated[key] = _CONFIG_KEYS[key](value)
    return validated


def _policy_rules(document: dict[str, Any], name: str) -> list[PolicyRule]:
    rules = []
    for index, rule in enumerate(document["spec"]["rules"]):
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("name"), str)
            or rule.get("effect") not in {"allow", "deny", "warn"}
            or not isinstance(rule.get("match", {}), dict)
        ):
            raise ValidationError(f"policy rule {index} is invalid in {name}")
        rules.append(
            PolicyRule(
                str(rule["name"]),
                rule["effect"],
                dict(rule.get("match", {})),
                str(rule.get("reason", "")),
            )
        )
    return rules


def _policy_documents(location: Path, namespace: str | None) -> list[tuple[str, dict[str, Any]]]:
    from omf.schema_registry import default_registry

    documents = []
    paths = (
        sorted(item for item in location.iterdir() if item.is_file()) if location.is_dir() else []
    )
    for path in paths:
        if path.suffix not in _POLICY_SUFFIXES:
            continue
        value = load_document(path.read_bytes())
        if not isinstance(value, dict):
            raise ValidationError(f"policy document must be one object: {path.name}")
        document = default_registry.validate_as(value, "Policy")
        if document["metadata"].get("namespace", namespace) != namespace:
            raise ValidationError(f"policy namespace does not match the project: {path.name}")
        documents.append((path.name, document))
    return documents


@dataclass(frozen=True)
class ProjectPolicy:
    engine: PolicyEngine
    config: dict[str, Any]
    documents: tuple[dict[str, Any], ...]
    digest: str
    directory: str

    @property
    def enforced(self) -> bool:
        return bool(self.documents)

    @property
    def dirty_worktree(self) -> str:
        return str(self.config.get("dirtyWorktree", "allow"))

    def authorize(self, context: dict[str, Any]) -> PolicyDecision:
        if not self.documents:
            return PolicyDecision(
                "allow",
                self.digest,
                ({"rule": "no-policy-documents", "effect": "allow", "reason": ""},),
            )
        return self.engine.evaluate(context)

    @classmethod
    def load(cls, root: str | Path, project: dict[str, Any]) -> ProjectPolicy:
        extensions = project.get("spec", {}).get("extensions", {})
        directory = str(extensions.get("policyDirectory", "policies"))
        if not directory or Path(directory).is_absolute() or ".." in Path(directory).parts:
            raise ValidationError("policyDirectory must be a relative path inside the project")
        namespace = project.get("metadata", {}).get("namespace")
        rules: list[PolicyRule] = []
        config: dict[str, Any] = {}
        documents: list[dict[str, Any]] = []
        for name, document in _policy_documents(Path(root) / directory, namespace):
            rules.extend(_policy_rules(document, name))
            for key, item in _validate_policy_config(
                dict(document["spec"].get("config", {}))
            ).items():
                if key in config and config[key] != item:
                    raise ValidationError(f"policy documents disagree on config key: {key}")
                config[key] = item
            documents.append({"path": name, "document": document})
        digest = sha256_digest({"directory": directory, "documents": documents})
        return cls(PolicyEngine(rules), config, tuple(documents), digest, directory)


@dataclass(frozen=True)
class BreakGlass:
    actor: str
    reason: str
    expires_at: str
    key_id: str
    signature: str

    def unsigned(self) -> dict[str, str]:
        return {
            "actor": self.actor,
            "reason": self.reason,
            "expiresAt": self.expires_at,
            "keyId": self.key_id,
        }


def promotion_gate(
    evidence: dict[str, Any],
    *,
    actor: str,
    break_glass: BreakGlass | None = None,
    public_key: bytes | None = None,
    now: datetime | None = None,
) -> PolicyDecision:
    checks = {
        "evaluation": bool(evidence.get("evaluation_passed")),
        "lineage": bool(evidence.get("lineage_complete")),
        "rights": bool(evidence.get("rights_valid")),
        "signatures": bool(evidence.get("signatures_valid")),
        "compatibility": bool(evidence.get("compatibility_passed")),
        "vulnerabilities": bool(evidence.get("vulnerabilities_valid")),
        "approvals": bool(evidence.get("approvals_valid")),
        "separation": bool(evidence.get("separation_of_duties")),
    }
    digest = sha256_digest({"gate": "promotion-v1", "checks": sorted(checks)})
    failures = [name for name, passed in checks.items() if not passed]
    if failures and break_glass is not None:
        if public_key is None or break_glass.actor != actor:
            raise IntegrityError("invalid break-glass actor or trust key")
        verify(public_key, break_glass.unsigned(), break_glass.signature)
        expiry = datetime.fromisoformat(break_glass.expires_at.replace("Z", "+00:00"))
        if expiry <= (now or datetime.now(UTC)):
            raise IntegrityError("break-glass authorization expired")
        return PolicyDecision(
            "warn",
            digest,
            ({"rule": "signed-break-glass", "effect": "warn", "reason": break_glass.reason},),
        )
    explanations = tuple(
        {"rule": name, "effect": "allow" if passed else "deny", "reason": "gate evidence"}
        for name, passed in checks.items()
    )
    return PolicyDecision("deny" if failures else "allow", digest, explanations)
