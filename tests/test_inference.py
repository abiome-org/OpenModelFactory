import pytest
from omf.errors import ValidationError
from omf.inference import (
    ConformanceRunner,
    ConformanceVector,
    InferenceRequest,
    InferenceResult,
    Part,
    Tolerance,
)


class Executor:
    def __init__(self, result):
        self.result = result

    def execute(self, request):
        return self.result


def _request():
    return InferenceRequest("m", "s", "r", "predict", (Part("x", [1]),))


def test_tolerance_pass_and_comparison_count():
    expected = InferenceResult((Part("y", [1.0], dtype="f32"),), "m", "s", "r")
    actual = InferenceResult((Part("y", [1.01], dtype="f32"),), "m", "s", "r")
    result = ConformanceRunner().run(
        [ConformanceVector(_request(), expected, {"y": Tolerance(0.02, 0)})], Executor(actual)
    )
    assert result.passed
    assert result.comparisons == 1


@pytest.mark.parametrize("actual", [Part("y", [[1.0]], dtype="f32"), Part("y", [1.0], dtype="f64")])
def test_shape_or_dtype_mismatch(actual):
    expected = InferenceResult((Part("y", [1.0], dtype="f32"),), "m", "s", "r")
    result = ConformanceRunner().run(
        [ConformanceVector(_request(), expected)],
        Executor(InferenceResult((actual,), "m", "s", "r")),
    )
    assert not result.passed


def test_custom_requires_method_name():
    with pytest.raises(ValidationError):
        InferenceRequest("m", "s", "r", "custom", ())
