from __future__ import annotations

from omf.sdk import ProtocolRequest, ProtocolResult, main


def validate(_request: ProtocolRequest) -> ProtocolResult:
    return ProtocolResult(status="ok", outputs={"protocol": "omf.module/v1"})


def run(request: ProtocolRequest) -> ProtocolResult:
    state = request.state
    value = float(request.inputs["input"])
    prediction = float(state["slope"]) * value + float(state["intercept"])
    return ProtocolResult(status="ok", outputs={"prediction": prediction}, state=state)


if __name__ == "__main__":
    raise SystemExit(main({"validate": validate, "run": run}))
