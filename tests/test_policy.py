from datetime import UTC, datetime, timedelta

from omf.policy import BreakGlass, PolicyEngine, PolicyRule, promotion_gate
from omf.security import SigningIdentity


def test_deny_overrides_allow_and_default_deny():
    engine = PolicyEngine(
        [
            PolicyRule("yes", "allow", {"action": "deploy"}),
            PolicyRule("no", "deny", {"actor": "bad"}),
        ]
    )
    assert engine.evaluate({"action": "deploy", "actor": "bad"}).outcome == "deny"
    assert engine.evaluate({"action": "read"}).outcome == "deny"


def test_signed_break_glass_converts_failed_gate_to_warning(tmp_path):
    identity = SigningIdentity(tmp_path / "key")
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    unsigned = {
        "actor": "operator",
        "reason": "incident",
        "expiresAt": expiry,
        "keyId": identity.key_id,
    }
    grant = BreakGlass("operator", "incident", expiry, identity.key_id, identity.sign(unsigned))
    decision = promotion_gate(
        {}, actor="operator", break_glass=grant, public_key=identity.public_bytes
    )
    assert decision.outcome == "warn"
