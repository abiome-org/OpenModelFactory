"""Detached local execution monitor that durably records command completion."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from types import FrameType

from omf.canonical import sha256_digest

_child: subprocess.Popen[bytes] | None = None
_stop_reason: str | None = None


def _signal_child(kind: signal.Signals) -> None:
    child = _child
    if child is None or child.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child.pid, kind)


def _force_kill() -> None:
    _signal_child(signal.SIGKILL)


def _handle_stop(signum: int, _frame: FrameType | None) -> None:
    global _stop_reason
    _stop_reason = f"signal:{signal.Signals(signum).name}"
    _signal_child(signal.SIGTERM)
    timer = threading.Timer(5.0, _force_kill)
    timer.daemon = True
    timer.start()


def _write_completion(
    path: Path, *, exit_code: int, reason: str, evidence: dict[str, object] | None = None
) -> None:
    value = {
        "exitCode": exit_code,
        "reason": reason,
        "finished": time.time(),
        "evidence": evidence or {},
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--completion", required=True, type=Path)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--attested-executable", nargs=2, action="append", default=[])
    parser.add_argument("--environment-digest")
    parser.add_argument("--argv-digest", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command.pop(0)
    if not command:
        parser.error("a command is required after --")
    evidence: dict[str, object] = {
        "environmentDigest": args.environment_digest,
        "argvDigest": sha256_digest(command),
        "executables": [],
    }
    if evidence["argvDigest"] != args.argv_digest:
        _write_completion(
            args.completion, exit_code=1, reason="argv-digest-mismatch", evidence=evidence
        )
        return 1
    observed = []
    for path_value, expected in args.attested_executable:
        path = Path(path_value)
        actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        observed.append({"path": str(path), "digest": actual})
        if actual != expected:
            evidence["executables"] = observed
            _write_completion(
                args.completion,
                exit_code=1,
                reason="executable-digest-mismatch",
                evidence=evidence,
            )
            return 1
    evidence["executables"] = observed

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    global _child
    _child = subprocess.Popen(command, start_new_session=True)
    try:
        exit_code = _child.wait(timeout=args.timeout)
        reason = _stop_reason or ("completed" if exit_code == 0 else "nonzero-exit")
    except subprocess.TimeoutExpired:
        reason = "timeout"
        _signal_child(signal.SIGTERM)
        try:
            exit_code = _child.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            _signal_child(signal.SIGKILL)
            exit_code = _child.wait()
    _write_completion(args.completion, exit_code=exit_code, reason=reason, evidence=evidence)
    return exit_code if 0 <= exit_code <= 255 else 1


if __name__ == "__main__":
    raise SystemExit(main())
