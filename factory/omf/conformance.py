"""Conservative signed conformance reports; evidence gaps never become claims."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from omf.canonical import sha256_digest
from omf.errors import IntegrityError, ValidationError
from omf.security import SigningIdentity, verify

_CORE_SCENARIOS = frozenset({1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 17})
_PROFILE_SCENARIOS = {
    "OMF-Core": _CORE_SCENARIOS,
    "OMF-Cluster": _CORE_SCENARIOS | {3, 14},
    "OMF-Airgap": _CORE_SCENARIOS | {13},
    "OMF-Federated": _CORE_SCENARIOS | {15},
}
_PROFILES = frozenset({*_PROFILE_SCENARIOS, "OMF-Frontier"})


def build_report(
    evidence: dict[str, Any], *, identity: SigningIdentity, spec_revision: str
) -> dict[str, Any]:
    required_shapes: dict[str, type[Any]] = {
        "suiteRevision": str,
        "manifests": list,
        "environment": dict,
        "hardware": dict,
        "rawResults": dict,
        "failures": list,
        "waivers": list,
    }
    for field, kind in required_shapes.items():
        if not isinstance(evidence.get(field), kind) or (
            field == "suiteRevision" and not evidence[field]
        ):
            raise ValidationError(f"conformance evidence requires {field} as {kind.__name__}")
    for field in ("manifests", "environment", "hardware", "rawResults"):
        if not evidence[field]:
            raise ValidationError(f"conformance evidence requires non-empty {field}")
    requested = evidence.get("profiles", [])
    if not isinstance(requested, list) or any(item not in _PROFILES for item in requested):
        raise ValidationError("conformance profiles contain an unsupported value")
    scenario_values = evidence.get("scenarios", [])
    if not isinstance(scenario_values, list) or any(
        not isinstance(item, dict) for item in scenario_values
    ):
        raise ValidationError("conformance scenarios must be an array of objects")
    scenarios: dict[int, dict[str, Any]] = {}
    for value in scenario_values:
        identifier = value.get("id")
        if not isinstance(identifier, int) or identifier < 1 or identifier > 17:
            raise ValidationError("conformance scenario id must be between 1 and 17")
        if not isinstance(value.get("passed"), bool) or not isinstance(value.get("evidence"), list):
            raise ValidationError("each conformance scenario requires passed and evidence fields")
        if value["passed"] and not value["evidence"]:
            raise ValidationError("passing conformance scenarios require evidence references")
        if identifier in scenarios:
            raise ValidationError("conformance scenario ids must be unique")
        scenarios[identifier] = value

    eligible: list[str] = []
    denied: dict[str, list[str]] = {}
    for profile in requested:
        if profile == "OMF-Frontier":
            continue
        missing = [
            f"scenario:{identifier}"
            for identifier in sorted(_PROFILE_SCENARIOS[profile])
            if not bool(scenarios.get(identifier, {}).get("passed"))
        ]
        if missing:
            denied[profile] = missing
        else:
            eligible.append(profile)

    if "OMF-Frontier" in requested:
        capacity = evidence.get("capacity", {})
        prerequisite = "OMF-Cluster" in eligible or "OMF-Federated" in eligible
        frontier_failures = []
        accelerators_tested = (
            capacity.get("acceleratorsTested") if isinstance(capacity, dict) else None
        )
        if not prerequisite:
            frontier_failures.append("profile:OMF-Cluster-or-OMF-Federated")
        if not isinstance(capacity, dict) or capacity.get("measured") is not True:
            frontier_failures.append("capacity:actual-measurement")
        if (
            not isinstance(accelerators_tested, int)
            or isinstance(accelerators_tested, bool)
            or accelerators_tested < 1024
        ):
            frontier_failures.append("capacity:accelerators>=1024")
        if not isinstance(capacity, dict) or not capacity.get("measurementEvidence"):
            frontier_failures.append("capacity:measurement-evidence")
        if frontier_failures:
            denied["OMF-Frontier"] = frontier_failures
        else:
            eligible.append("OMF-Frontier")

    report = {
        "apiVersion": "omf.dev/conformance/v1alpha1",
        "specRevision": spec_revision,
        "suiteRevision": evidence.get("suiteRevision"),
        "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "profilesRequested": requested,
        "profilesClaimed": eligible,
        "profilesDenied": denied,
        "capabilityProfiles": evidence.get("capabilityProfiles", []),
        "manifests": evidence.get("manifests", []),
        "environment": evidence.get("environment", {}),
        "hardware": evidence.get("hardware", {}),
        "capacity": evidence.get("capacity"),
        "scenarios": scenario_values,
        "rawResults": evidence.get("rawResults", {}),
        "failures": evidence.get("failures", []),
        "waivers": evidence.get("waivers", []),
    }
    digest = sha256_digest(report)
    signed_fields = {"report": report, "digest": digest, "keyId": identity.key_id}
    return {**signed_fields, "signature": identity.sign(signed_fields)}


def verify_report(value: dict[str, Any], public_key: bytes) -> dict[str, Any]:
    try:
        report = value["report"]
        digest = value["digest"]
        key_id = value["keyId"]
        signature = value["signature"]
    except KeyError as exc:
        raise ValidationError("signed conformance report is incomplete") from exc
    if digest != sha256_digest(report):
        raise IntegrityError("conformance report digest mismatch")
    expected_key_id = sha256_digest({"publicKey": base64.b64encode(public_key).decode()})
    if key_id != expected_key_id:
        raise IntegrityError("conformance report key identity mismatch")
    fields = {"report": report, "digest": digest, "keyId": key_id}
    verify(public_key, fields, str(signature))
    return {
        "valid": True,
        "digest": digest,
        "keyId": key_id,
        "profilesClaimed": report.get("profilesClaimed", []),
        "profilesDenied": report.get("profilesDenied", {}),
    }
