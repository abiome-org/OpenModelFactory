"""Typed environment adapter and idempotent session manager."""

from __future__ import annotations

import copy
import json
import os
import resource
import selectors
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omf.canonical import canonical_json
from omf.errors import NotFoundError, ValidationError


class EnvironmentAdapter(ABC):
    capabilities: frozenset[str] = frozenset()

    @abstractmethod
    def create(
        self, spec_revision: str, task_revision: str, seed: int, limits: dict[str, Any]
    ) -> Any: ...
    @abstractmethod
    def observe(self, session: Any) -> dict[str, Any]: ...
    @abstractmethod
    def step(self, session: Any, action: dict[str, Any]) -> dict[str, Any]: ...
    def snapshot(self, session: Any) -> dict[str, Any]:
        raise ValidationError("snapshot unsupported")

    @abstractmethod
    def close(self, session: Any) -> None: ...


@dataclass
class Session:
    id: str
    backend: Any
    limits: dict[str, Any]
    created: float
    steps: int = 0
    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    closed: bool = False


class SessionManager:
    def __init__(
        self, adapter: EnvironmentAdapter, verifier: EnvironmentAdapter | None = None
    ) -> None:
        self.adapter, self.verifier = adapter, verifier
        self.sessions: dict[str, Session] = {}

    def create(
        self, spec_revision: str, task_revision: str, seed: int, limits: dict[str, Any]
    ) -> Session:
        session = Session(
            str(uuid.uuid4()),
            self.adapter.create(spec_revision, task_revision, seed, limits),
            dict(limits),
            time.monotonic(),
        )
        self.sessions[session.id] = session
        assert isinstance(session, Session)
        return session

    def _get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None or session.closed:
            raise NotFoundError("environment session not found")
        deadline = session.limits.get("deadline_seconds")
        if deadline is not None and time.monotonic() - session.created > float(deadline):
            raise ValidationError("session deadline exceeded")
        return session

    def observe(self, session_id: str) -> dict[str, Any]:
        return self.adapter.observe(self._get(session_id).backend)

    def step(self, session_id: str, action: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        session = self._get(session_id)
        if idempotency_key in session.responses:
            return copy.deepcopy(session.responses[idempotency_key])
        if session.steps >= int(session.limits.get("max_steps", 2**63 - 1)):
            raise ValidationError("session step quota exceeded")
        response = self.adapter.step(session.backend, action)
        session.responses[idempotency_key] = copy.deepcopy(response)
        session.steps += 1
        return copy.deepcopy(response)

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self.adapter.snapshot(self._get(session_id).backend)

    def evaluate(self, session_id: str, verifier_revision: str) -> dict[str, Any]:
        """Verifier receives only an observation and revision, never acting backend/secrets."""
        if self.verifier is None:
            raise ValidationError("verifier not configured")
        observation = self.observe(session_id)
        backend = self.verifier.create(verifier_revision, verifier_revision, 0, {})
        try:
            return self.verifier.step(backend, {"observation": observation})
        finally:
            self.verifier.close(backend)

    def close(self, session_id: str) -> None:
        session = self._get(session_id)
        self.adapter.close(session.backend)
        session.closed = True


@dataclass
class _SubprocessSession:
    process: subprocess.Popen[bytes]
    timeout: float
    max_response_bytes: int
    stderr: Any
    lock: threading.Lock = field(default_factory=threading.Lock)
    buffer: bytearray = field(default_factory=bytearray)


class LocalSubprocessEnvironment(EnvironmentAdapter):
    """JSON-lines environment worker with bounded I/O and process resource isolation."""

    capabilities = frozenset({"process", "rlimit", "session-rpc", "bounded-io"})

    def __init__(self, argv: Sequence[str], *, cwd: str | Path | None = None) -> None:
        if not argv or any(not value or "\x00" in value for value in argv):
            raise ValidationError("environment argv must contain non-empty arguments")
        self.argv = tuple(argv)
        self.cwd = Path(cwd).resolve() if cwd is not None else Path.cwd()

    def create(
        self, spec_revision: str, task_revision: str, seed: int, limits: dict[str, Any]
    ) -> _SubprocessSession:
        timeout = float(limits.get("request_timeout_seconds", 30))
        maximum = int(limits.get("max_response_bytes", 1024 * 1024))
        if timeout <= 0 or maximum <= 0:
            raise ValidationError("environment I/O limits must be positive")
        process_limits = dict(limits.get("process", {}))

        def setup() -> None:
            os.setsid()
            for key, kind in {
                "cpu_seconds": resource.RLIMIT_CPU,
                "address_space": resource.RLIMIT_AS,
                "processes": resource.RLIMIT_NPROC,
                "file_size": resource.RLIMIT_FSIZE,
            }.items():
                if key in process_limits:
                    value = int(process_limits[key])
                    resource.setrlimit(kind, (value, value))

        stderr = tempfile.TemporaryFile()  # noqa: SIM115 - session owns the file lifetime
        try:
            process = subprocess.Popen(
                self.argv,
                cwd=self.cwd,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key in {"PATH", "HOME", "LANG", "TZ"}
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                preexec_fn=setup,
            )
        except Exception:
            stderr.close()
            raise
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        session = _SubprocessSession(process, timeout, maximum, stderr)
        try:
            self._request(
                session,
                {
                    "operation": "create",
                    "specRevision": spec_revision,
                    "taskRevision": task_revision,
                    "seed": seed,
                    "limits": limits,
                },
            )
        except Exception:
            self._terminate(session)
            raise
        return session

    def _request(self, session: _SubprocessSession, request: dict[str, Any]) -> dict[str, Any]:
        with session.lock:
            if session.process.poll() is not None:
                raise ValidationError("environment worker exited")
            assert session.process.stdin is not None
            assert session.process.stdout is not None
            payload = canonical_json(request) + b"\n"
            session.process.stdin.write(payload)
            session.process.stdin.flush()
            deadline = time.monotonic() + session.timeout
            selector = selectors.DefaultSelector()
            selector.register(session.process.stdout, selectors.EVENT_READ)
            try:
                while b"\n" not in session.buffer:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValidationError("environment worker response timeout")
                    block = os.read(session.process.stdout.fileno(), 65536)
                    if not block:
                        raise ValidationError("environment worker closed its response stream")
                    session.buffer.extend(block)
                    if len(session.buffer) > session.max_response_bytes:
                        raise ValidationError("environment worker response exceeds limit")
            finally:
                selector.close()
            line, _, remainder = session.buffer.partition(b"\n")
            session.buffer = bytearray(remainder)
            try:
                response = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError("environment worker returned invalid JSON") from exc
            if not isinstance(response, dict):
                raise ValidationError("environment worker response must be an object")
            if response.get("error"):
                raise ValidationError(f"environment worker error: {response['error']}")
            return response

    def observe(self, session: _SubprocessSession) -> dict[str, Any]:
        return self._request(session, {"operation": "observe"})

    def step(self, session: _SubprocessSession, action: dict[str, Any]) -> dict[str, Any]:
        return self._request(session, {"operation": "step", "action": action})

    def snapshot(self, session: _SubprocessSession) -> dict[str, Any]:
        return self._request(session, {"operation": "snapshot"})

    @staticmethod
    def _terminate(session: _SubprocessSession) -> None:
        if session.process.poll() is None:
            os.killpg(session.process.pid, signal.SIGTERM)
            try:
                session.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(session.process.pid, signal.SIGKILL)
                session.process.wait(timeout=1)
        if session.process.stdin is not None:
            session.process.stdin.close()
        if session.process.stdout is not None:
            session.process.stdout.close()
        session.stderr.close()

    def close(self, session: _SubprocessSession) -> None:
        try:
            if session.process.poll() is None:
                self._request(session, {"operation": "close"})
        finally:
            self._terminate(session)
