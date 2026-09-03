"""Model-neutral inference protocol and numerical train/serve compatibility."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from omf.errors import ValidationError

Method = Literal["predict", "generate", "embed", "score", "custom"]


@dataclass(frozen=True)
class Part:
    name: str
    value: Any
    media_type: str = "application/json"
    dtype: str | None = None
    dimensions: tuple[int, ...] | None = None


@dataclass(frozen=True)
class InferenceRequest:
    model_revision: str
    state_revision: str
    runtime_revision: str
    method: Method
    inputs: tuple[Part, ...]
    session_state: dict[str, Any] | None = None
    seed: int | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    deadline: str | None = None
    priority: int = 0
    trace_context: dict[str, str] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    streaming: bool = False
    custom_method: str | None = None

    def __post_init__(self) -> None:
        if self.method == "custom" and not self.custom_method:
            raise ValidationError("custom inference requires custom_method")


@dataclass(frozen=True)
class InferenceResult:
    outputs: tuple[Part, ...]
    model_revision: str
    state_revision: str
    runtime_revision: str
    policy_state_revision: str | None = None
    realized_parameters: dict[str, Any] = field(default_factory=dict)
    accounting: dict[str, int | float] = field(default_factory=dict)
    finish_status: str = "completed"
    trace_id: str | None = None
    session_state: dict[str, Any] | None = None
    intermediates: dict[str, Part] = field(default_factory=dict)


class InferenceExecutor(Protocol):
    def execute(self, request: InferenceRequest) -> InferenceResult: ...


@dataclass(frozen=True)
class Tolerance:
    absolute: float = 1e-6
    relative: float = 1e-5
    dtype: str | None = None


@dataclass(frozen=True)
class CompatibilityVector:
    request: InferenceRequest
    expected: InferenceResult
    tolerances: dict[str, Tolerance] = field(default_factory=dict)
    required_intermediates: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompatibilityResult:
    passed: bool
    failures: tuple[str, ...]
    comparisons: int
    derived_revision: str | None = None


def _flatten(value: Any, path: str) -> list[tuple[str, float]]:
    if isinstance(value, bool) or not isinstance(value, (int, float, list, tuple)):
        raise ValidationError(f"non-numerical compatibility value at {path}")
    if isinstance(value, (int, float)):
        return [(path, float(value))]
    result: list[tuple[str, float]] = []
    for index, child in enumerate(value):
        result.extend(_flatten(child, f"{path}[{index}]"))
    return result


def _shape(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    child = _shape(value[0]) if value else ()
    if any(_shape(item) != child for item in value):
        raise ValidationError("ragged tensor-like output")
    return (len(value), *child)


class CompatibilityRunner:
    def run(
        self,
        vectors: list[CompatibilityVector],
        executor: InferenceExecutor,
        *,
        derived_revision: str | None = None,
    ) -> CompatibilityResult:
        failures: list[str] = []
        comparisons = 0
        for vector_index, vector in enumerate(vectors):
            actual = executor.execute(vector.request)
            if (actual.model_revision, actual.state_revision) != (
                vector.expected.model_revision,
                vector.expected.state_revision,
            ):
                failures.append(f"vector {vector_index}: model/state identity mismatch")
            expected_parts = {part.name: part for part in vector.expected.outputs}
            actual_parts = {part.name: part for part in actual.outputs}
            for name, expected in expected_parts.items():
                actual_part = actual_parts.get(name)
                if actual_part is None:
                    failures.append(f"vector {vector_index}: missing output {name}")
                    continue
                failures.extend(
                    self._compare(
                        expected, actual_part, vector.tolerances.get(name, Tolerance()), name
                    )
                )
                comparisons += len(_flatten(expected.value, name))
            for name in vector.required_intermediates:
                if name not in actual.intermediates or name not in vector.expected.intermediates:
                    failures.append(f"vector {vector_index}: missing intermediate {name}")
                else:
                    failures.extend(
                        self._compare(
                            vector.expected.intermediates[name],
                            actual.intermediates[name],
                            vector.tolerances.get(name, Tolerance()),
                            name,
                        )
                    )
        return CompatibilityResult(not failures, tuple(failures), comparisons, derived_revision)

    @staticmethod
    def _compare(expected: Part, actual: Part, tolerance: Tolerance, name: str) -> list[str]:
        failures: list[str] = []
        if expected.dtype != actual.dtype or (tolerance.dtype and actual.dtype != tolerance.dtype):
            failures.append(f"{name}: dtype mismatch")
        if (
            _shape(expected.value) != _shape(actual.value)
            or expected.dimensions != actual.dimensions
        ):
            failures.append(f"{name}: dimensions mismatch")
            return failures
        for (path, left), (_, right) in zip(
            _flatten(expected.value, name), _flatten(actual.value, name), strict=True
        ):
            if not math.isclose(
                left, right, abs_tol=tolerance.absolute, rel_tol=tolerance.relative
            ):
                failures.append(
                    f"{path}: {right} outside abs={tolerance.absolute} rel={tolerance.relative}"
                )
        return failures
