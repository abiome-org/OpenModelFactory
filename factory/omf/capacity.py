"""Capacity benchmark measurements."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CapacityReport:
    accelerators_tested: int
    duration_seconds: float
    event_throughput: float
    artifact_throughput: float
    control_throughput: float
    failures: int
    restore_seconds: float


class CapacityHarness:
    def run(
        self,
        *,
        accelerators: int,
        operations: int,
        event: Callable[[], None],
        artifact: Callable[[], None],
        control: Callable[[], None],
        restore: Callable[[], None],
    ) -> CapacityReport:
        failures = 0
        throughputs = []
        started_all = time.perf_counter()
        for operation in (event, artifact, control):
            started = time.perf_counter()
            for _ in range(operations):
                try:
                    operation()
                except Exception:
                    failures += 1
            throughputs.append(operations / max(time.perf_counter() - started, 1e-12))
        started = time.perf_counter()
        restore()
        restore_seconds = time.perf_counter() - started
        return CapacityReport(
            accelerators,
            time.perf_counter() - started_all,
            throughputs[0],
            throughputs[1],
            throughputs[2],
            failures,
            restore_seconds,
        )
