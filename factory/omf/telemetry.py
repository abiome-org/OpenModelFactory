"""Local-first bounded structured telemetry, explicitly not provenance."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SENSITIVE = {"secret", "token", "password", "prompt", "input", "output", "tensor", "authorization"}


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str

    def headers(self) -> dict[str, str]:
        return {"traceparent": f"00-{self.trace_id}-{self.span_id}-01"}


class TelemetrySink:
    """JSONL sink. An exporter is called only when explicitly supplied."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 1_000_000,
        max_labels: int = 16,
        exporter: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.path, self.max_bytes, self.max_labels, self.exporter = (
            Path(path),
            max_bytes,
            max_labels,
            exporter,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        signal: str,
        name: str,
        *,
        fields: dict[str, Any] | None = None,
        labels: dict[str, str] | None = None,
        trace: TraceContext | None = None,
    ) -> dict[str, Any]:
        labels = labels or {}
        if len(labels) > self.max_labels:
            raise ValueError("telemetry label cardinality bound exceeded")
        clean = {
            key: "[REDACTED]" if key.lower() in _SENSITIVE else value
            for key, value in (fields or {}).items()
        }
        record = {
            "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "signal": signal,
            "name": name,
            "labels": dict(sorted(labels.items())),
            "fields": clean,
            "traceId": trace.trace_id if trace else None,
            "provenance": False,
        }
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        if self.path.exists() and self.path.stat().st_size + len(encoded.encode()) > self.max_bytes:
            rotated = self.path.with_suffix(self.path.suffix + ".1")
            if rotated.exists():
                rotated.unlink()
            os.replace(self.path, rotated)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(encoded)
        if self.exporter is not None:
            self.exporter(record)
        return record

    def metric(self, name: str, value: float, **kwargs: Any) -> dict[str, Any]:
        fields = dict(kwargs.pop("fields", {}))
        fields["value"] = value
        return self.emit("metric", name, fields=fields, **kwargs)

    def log(self, name: str, **kwargs: Any) -> dict[str, Any]:
        return self.emit("log", name, **kwargs)

    def trace(self, name: str, **kwargs: Any) -> dict[str, Any]:
        return self.emit("trace", name, **kwargs)
