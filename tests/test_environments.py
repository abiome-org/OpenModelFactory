import sys
import textwrap
from typing import Any

from omf.environments import EnvironmentAdapter, LocalSubprocessEnvironment, SessionManager


class Fake(EnvironmentAdapter):
    def __init__(self):
        self.calls = 0

    def create(self, *args: Any):
        return {}

    def observe(self, s):
        return {"public": 1}

    def step(self, s, a):
        self.calls += 1
        return {"value": self.calls, "action": a}

    def close(self, s):
        pass


def test_idempotence_and_verifier_separation():
    actor, verifier = Fake(), Fake()
    manager = SessionManager(actor, verifier)
    session = manager.create("s", "t", 1, {})
    assert manager.step(session.id, {}, "k") == manager.step(session.id, {}, "k")
    assert actor.calls == 1
    result = manager.evaluate(session.id, "v")
    assert result["action"] == {"observation": {"public": 1}}


def test_local_subprocess_environment_protocol(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(
        textwrap.dedent(
            """
            import json, sys
            value = 0
            for line in sys.stdin:
                request = json.loads(line)
                operation = request["operation"]
                if operation == "create":
                    response = {"created": True, "seed": request["seed"]}
                elif operation == "observe":
                    response = {"value": value}
                elif operation == "step":
                    value += request["action"]["increment"]
                    response = {"value": value}
                elif operation == "snapshot":
                    response = {"state": {"value": value}}
                elif operation == "close":
                    print(json.dumps({"closed": True}), flush=True)
                    break
                print(json.dumps(response), flush=True)
            """
        )
    )
    manager = SessionManager(
        LocalSubprocessEnvironment([sys.executable, str(worker)], cwd=tmp_path)
    )
    session = manager.create(
        "environment-one",
        "task-one",
        7,
        {"max_steps": 2, "request_timeout_seconds": 2, "max_response_bytes": 4096},
    )
    assert manager.observe(session.id) == {"value": 0}
    assert manager.step(session.id, {"increment": 2}, "step-one") == {"value": 2}
    assert manager.snapshot(session.id) == {"state": {"value": 2}}
    manager.close(session.id)
