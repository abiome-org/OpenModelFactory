from omf.evaluation import EvaluationRunner, EvaluationSpec, checkpoint_trigger
from omf.inference import InferenceRequest, InferenceResult, Part


class Inference:
    def execute(self, request):
        if request.seed == 2:
            raise TimeoutError("deadline")
        return InferenceResult((Part("score", float(request.seed or 0)),), "m", "s", "r")


class Verifier:
    def verify(self, case, output):
        return {"score": output.outputs[0].value + case}


def _request(_case, seed):
    return InferenceRequest("m", "s", "r", "score", (), seed=seed)


def test_evaluation_retains_distribution_slices_uncertainty_and_failures():
    spec = EvaluationSpec(
        "state",
        (0.0, 1.0),
        _request,
        seeds=(1, 2),
        repeats=2,
        slices={"positive": lambda case: case > 0},
        thresholds={"score": 0.5},
        contamination_declaration={"checked": True},
    )
    result = EvaluationRunner().run(spec, Inference(), Verifier())
    assert result.distributions["score"] == (1.0, 2.0)
    assert result.slice_distributions["positive"]["score"] == (2.0,)
    assert len(result.failures) == 2
    assert result.confidence_intervals["score"][0] < result.means["score"]
    assert not result.passed


def test_checkpoint_trigger_does_not_undo_checkpoint_on_enqueue_failure():
    assert not checkpoint_trigger(lambda _revision: (_ for _ in ()).throw(RuntimeError()), "cp")
    recorded = []
    assert checkpoint_trigger(recorded.append, "cp")
    assert recorded == ["cp"]
