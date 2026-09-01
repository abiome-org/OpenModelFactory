from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from omf.config import ProjectPaths, bootstrap
from omf.conformance import build_report, verify_report
from omf.errors import IntegrityError, ValidationError
from omf.factory import Factory
from omf.security import SigningIdentity


def _evidence():
    return {
        "suiteRevision": "suite:one",
        "profiles": ["OMF-Core", "OMF-Frontier"],
        "capabilityProfiles": ["example-statistical"],
        "manifests": ["sha256:" + "a" * 64],
        "environment": {"os": "test"},
        "hardware": {"accelerators": 1},
        "capacity": {"measured": False, "acceleratorsTested": 1},
        "scenarios": [
            {"id": identifier, "passed": True, "evidence": [f"result:{identifier}"]}
            for identifier in (1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 17)
        ],
        "rawResults": {"suite": "passed"},
        "failures": [],
        "waivers": [],
    }


def test_signed_conformance_claims_only_complete_profiles(tmp_path):
    identity = SigningIdentity(tmp_path / "identity.key")
    signed = build_report(_evidence(), identity=identity, spec_revision="sha256:spec")
    assert signed["report"]["profilesClaimed"] == ["OMF-Core"]
    assert "OMF-Frontier" in signed["report"]["profilesDenied"]
    verified = verify_report(signed, identity.public_bytes)
    assert verified["valid"]
    assert verified["profilesClaimed"] == ["OMF-Core"]

    tampered = deepcopy(signed)
    tampered["report"]["hardware"] = {"accelerators": 2048}
    with pytest.raises(IntegrityError, match="digest"):
        verify_report(tampered, identity.public_bytes)


def test_conformance_rejects_incomplete_or_duplicate_evidence(tmp_path):
    identity = SigningIdentity(tmp_path / "identity.key")
    incomplete = _evidence()
    incomplete.pop("rawResults")
    with pytest.raises(ValidationError, match="rawResults"):
        build_report(incomplete, identity=identity, spec_revision="sha256:spec")
    duplicate = _evidence()
    duplicate["scenarios"].append(duplicate["scenarios"][0])
    with pytest.raises(ValidationError, match="unique"):
        build_report(duplicate, identity=identity, spec_revision="sha256:spec")
    unsupported_claim = _evidence()
    unsupported_claim["scenarios"][0]["evidence"] = []
    with pytest.raises(ValidationError, match="evidence references"):
        build_report(unsupported_claim, identity=identity, spec_revision="sha256:spec")


def test_factory_commits_and_independently_verifies_conformance_report(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "omf.yaml").write_text(
        """apiVersion: omf.dev/v1alpha1
kind: Project
metadata: {name: conformance-test, namespace: local/conformance-test}
spec: {owners: [operator], extensions: {}}
"""
    )
    (root / "SPEC.md").write_text(Path("SPEC.md").read_text())
    paths = ProjectPaths(root)
    bootstrap(paths)
    evidence_path = root / "evidence.yaml"
    evidence_path.write_text(yaml.safe_dump(_evidence()))
    report_path = root / "report.json"

    with Factory(paths) as factory:
        result = factory.create_conformance_report(evidence_path, report_path)
        assert result["report"]["profilesClaimed"] == ["OMF-Core"]
        assert result["artifactManifest"].startswith("sha256:")
        assert factory.verify_conformance_report(report_path)["valid"]
