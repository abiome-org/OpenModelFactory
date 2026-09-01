import pytest
from omf.errors import IntegrityError
from omf.releases import ReleaseBuilder, verify_release
from omf.security import SigningIdentity


def _manifest():
    return {
        "model": {},
        "state": {},
        "runtime": {},
        "workload": {},
        "binding": {},
        "dataSummary": [],
        "evaluations": [],
        "limitations": [],
        "risk": {},
        "intendedUse": "test",
        "prohibitedUse": [],
        "conformance": {},
        "sbom": {},
        "provenance": {},
        "vulnerabilities": {},
        "deployment": {},
        "rollback": {},
        "licenses": [],
    }


def test_complete_release_signing_and_tamper_detection(tmp_path):
    identity = SigningIdentity(tmp_path / "key")
    release = ReleaseBuilder(identity).build(_manifest())
    verify_release(release, identity.public_bytes)
    release.manifest["intendedUse"] = "tampered"
    with pytest.raises(IntegrityError):
        verify_release(release, identity.public_bytes)


def test_weights_only_release_is_rejected(tmp_path):
    with pytest.raises(IntegrityError, match="incomplete"):
        ReleaseBuilder(SigningIdentity(tmp_path / "key")).build({"model": {}})
