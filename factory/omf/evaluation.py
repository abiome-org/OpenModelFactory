"""Independent evaluation execution retaining distributions and partial failures."""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from omf.canonical import sha256_digest
from omf.inference import InferenceExecutor, InferenceRequest


class Verifier(Protocol):
    def verify(self, case: Any, output: Any) -> dict[str, float]: ...


@dataclass(frozen=True)
class EvaluationSpec:
    model_state: str
    cases: tuple[Any, ...]
    request_factory: Callable[[Any, int], InferenceRequest]
    seeds: tuple[int, ...] = (0,)
    repeats: int = 1
    slices: dict[str, Callable[[Any], bool]] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    contamination_declaration: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class EvaluationResult:
    spec_digest: str
    distributions: dict[str, tuple[float, ...]]
    means: dict[str, float]
    confidence_intervals: dict[str, tuple[float, float]]
    slice_distributions: dict[str, dict[str, tuple[float, ...]]]
    failures: tuple[dict[str, Any], ...]
    resource_usage: dict[str, float]
    passed: bool
    contamination_declaration: dict[str, Any]


class EvaluationRunner:
    def run(
        self, spec: EvaluationSpec, inference: InferenceExecutor, verifier: Verifier
    ) -> EvaluationResult:
        values: dict[str, list[float]] = {}
        sliced: dict[str, dict[str, list[float]]] = {name: {} for name in spec.slices}
        failures: list[dict[str, Any]] = []
        calls = 0
        for case_index, case in enumerate(spec.cases):
            for repeat in range(spec.repeats):
                seed = spec.seeds[repeat % len(spec.seeds)]
                try:
                    result = inference.execute(spec.request_factory(case, seed))
                    if result.finish_status not in {"completed", "stop", "success"}:
                        raise RuntimeError(f"protocol finish status: {result.finish_status}")
                    scores = verifier.verify(case, result)
                    if not scores or any(not math.isfinite(score) for score in scores.values()):
                        raise ValueError("invalid verifier score")
                    for metric, score in scores.items():
                        values.setdefault(metric, []).append(score)
                        for name, predicate in spec.slices.items():
                            if predicate(case):
                                sliced[name].setdefault(metric, []).append(score)
                except TimeoutError as exc:
                    failures.append(
                        {
                            "case": case_index,
                            "repeat": repeat,
                            "kind": "timeout",
                            "message": str(exc),
                        }
                    )
                except Exception as exc:  # adapters are untrusted protocol boundaries
                    failures.append(
                        {
                            "case": case_index,
                            "repeat": repeat,
                            "kind": "invalid_or_protocol",
                            "message": str(exc),
                        }
                    )
                calls += 1
        frozen = {key: tuple(item) for key, item in values.items()}
        means = {key: statistics.fmean(item) for key, item in frozen.items()}
        cis = {key: self._ci(item) for key, item in frozen.items()}
        passed = not failures and all(
            means.get(key, -math.inf) >= limit for key, limit in spec.thresholds.items()
        )
        descriptor = {
            "modelState": spec.model_state,
            "cases": len(spec.cases),
            "seeds": spec.seeds,
            "repeats": spec.repeats,
            "contamination": spec.contamination_declaration,
        }
        return EvaluationResult(
            sha256_digest(descriptor),
            frozen,
            means,
            cis,
            {
                name: {metric: tuple(scores) for metric, scores in metrics.items()}
                for name, metrics in sliced.items()
            },
            tuple(failures),
            {"inference_calls": float(calls)},
            passed,
            dict(spec.contamination_declaration),
        )

    @staticmethod
    def _ci(values: tuple[float, ...]) -> tuple[float, float]:
        mean = statistics.fmean(values)
        if len(values) < 2:
            return mean, mean
        margin = 1.96 * statistics.stdev(values) / math.sqrt(len(values))
        return mean - margin, mean + margin


def checkpoint_trigger(enqueue: Callable[[str], None], checkpoint_revision: str) -> bool:
    """Best-effort scheduling; checkpoint commit is already complete and remains so."""
    try:
        enqueue(checkpoint_revision)
    except Exception:
        return False
    return True
