import pytest
from omf.errors import ValidationError
from omf.sampler import DeterministicSampler, MixSpec, SamplerState, Source


def test_worker_invariance_amendment_and_replay(tmp_path):
    mix = MixSpec("a", (Source("x", 1), Source("y", 1)), "seed")
    one = DeterministicSampler(mix)
    expected = [one.sample(i) for i in range(100)]
    two = DeterministicSampler(mix)
    assert expected == [two.sample(i) for i in range(100)]
    newer = MixSpec("b", (Source("z", 1),), "seed")
    two.amend(newer, 100)
    assert two.sample(100) == "z"
    path = tmp_path / "state"
    two.state.save(path)
    assert SamplerState.load(path).amendments == two.state.amendments


def test_lease_consumption_distribution_exhaustion_and_validation():
    with pytest.raises(ValidationError):
        MixSpec("bad", (Source("x", 0),), "seed")
    with pytest.raises(ValidationError):
        MixSpec("bad", (Source("x", 1), Source("x", 2)), "seed")
    mix = MixSpec("one", (Source("x", 1, size=2),), "seed", replacement=False)
    sampler = DeterministicSampler(mix)
    lease = sampler.lease("worker-one", 2)
    assert sampler.consume(lease) == ["x", "x"]
    sampler.acknowledge(lease)
    assert lease.acknowledged
    assert sampler.observed_distribution() == {"x": 1.0}
    sampler.redistribute(["worker-one", "worker-two"])
    assert sampler.state.redistribution_history[-1]["workers"] == ["worker-one", "worker-two"]
    with pytest.raises(ValidationError, match="exhausted"):
        sampler.sample(2)
    with pytest.raises(ValidationError, match="consumed"):
        sampler.amend(MixSpec("two", (Source("y", 1),), "seed"), 1)

    wrapping = DeterministicSampler(
        MixSpec(
            "wrap",
            (Source("x", 1, size=1),),
            "seed",
            replacement=False,
            exhaustion="wrap",
        )
    )
    wrapping.state.source_cursors["x"] = 2
    assert wrapping.sample(2) == "x"
    stopping = DeterministicSampler(
        MixSpec(
            "stop",
            (Source("x", 1, size=1),),
            "seed",
            replacement=False,
            exhaustion="stop",
        )
    )
    stopping.state.source_cursors["x"] = 1
    with pytest.raises(StopIteration):
        stopping.sample(1)
