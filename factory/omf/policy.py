"""Deterministic deny-overrides authorization and promotion gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from omf.canonical import sha256_digest
from omf.errors import IntegrityError
from omf.security import verify


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
