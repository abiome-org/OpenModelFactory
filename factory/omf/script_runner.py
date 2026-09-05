from __future__ import annotations

import json
import math
import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from omf.canonical import load_document, portable_relative_path
from omf.sdk import ProtocolRequest, ProtocolResult, main


def _output_path(root: Path, relative: str) -> Path:
    portable_relative_path(relative, "script output")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("script output escapes its output directory")
    return path


def _arguments(request: ProtocolRequest, output: Path) -> list[str]:
    inputs = {
        key: value["path"] if isinstance(value, dict) and "path" in value else value
        for key, value in request.inputs.items()
    }
    substitutions = {
        "inputs": inputs,
        "parameters": request.config["parameters"],
        "output": str(output),
    }
    return [argument.format_map(substitutions) for argument in request.config["command"]]


def _metrics(path: Path) -> dict[str, Any]:
    values = load_document(path.read_bytes())
    if not isinstance(values, dict) or not all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in values.values()
    ):
        raise ValueError("metrics must be a JSON object of finite numbers or boolean checks")
    return values


def run(request: ProtocolRequest) -> ProtocolResult:
    output = Path(os.environ["OMF_RESULT_FILE"]).resolve().parent / "outputs"
    output.mkdir(exist_ok=True)
    arguments = _arguments(request, output)
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    environment = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
    }
    subprocess.run(arguments, check=True, env=environment)
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    elapsed = time.monotonic() - started
    values = (
        _metrics(_output_path(output, request.config["metrics"]))
        if request.config.get("metrics")
        else {}
    )
    for name in request.config.get("metricNames", []):
        if name not in values or isinstance(values[name], bool):
            raise ValueError(f"declared metric must be a finite number: {name}")
    artifacts = [
        {
            "name": name,
            "path": str(_output_path(output, path)),
            "kind": "model" if name == "model" else "stage-output",
        }
        for name, path in request.config.get("artifacts", {}).items()
    ]
    examples = request.config.get("examples")
    if examples:
        path = _output_path(output, examples)
        records = load_document(path.read_bytes())
        if not isinstance(records, list) or not all(
            isinstance(item, dict) and isinstance(item.get("id"), (str, int)) for item in records
        ):
            raise ValueError("examples must be a JSON list of records with stable ids")
        if len({str(item["id"]) for item in records}) != len(records):
            raise ValueError("example ids must be unique")
        artifacts.append({"name": "examples", "path": str(path), "kind": "evaluation-examples"})
    measurement = {
        "wallSeconds": elapsed,
        "cpuSeconds": usage.ru_utime + usage.ru_stime - before.ru_utime - before.ru_stime,
    }
    measurement_path = _output_path(output.parent, "measurement.json")
    measurement_path.write_text(json.dumps(measurement))
    artifacts.append({"name": "measurement", "path": str(measurement_path), "kind": "measurement"})
    return ProtocolResult(status="ok", outputs=values, artifacts=artifacts)


if __name__ == "__main__":
    raise SystemExit(main({"validate": lambda _: ProtocolResult(status="ok"), "run": run}))
