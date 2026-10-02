import json

import numpy as np
import pytest

from verdy.odd import satisfies
from verdy.sampler import (
    ImportanceSampler,
    LogReplaySampler,
    MonteCarloSampler,
    SamplingError,
    StratifiedSampler,
    make_sampler,
)


@pytest.mark.parametrize("cls", [MonteCarloSampler, StratifiedSampler, ImportanceSampler])
def test_deterministic_and_feasible(odd, cls):
    a = [s.to_dict() for s in cls(odd, seed=3).sample(50)]
    b = [s.to_dict() for s in cls(odd, seed=3).sample(50)]
    c = [s.to_dict() for s in cls(odd, seed=4).sample(50)]
    assert a == b and a != c
    sampler = cls(odd, seed=3)
    for s in sampler.sample(200):
        assert satisfies(sampler.constraints, s.params)
        assert 20 <= s.params["lighting"] <= 1000
        assert s.params["floor_type"] in ("tile", "wood", "carpet")
        assert isinstance(s.params["wet"], bool)


def test_ids_and_seeds_continue_across_batches(odd):
    sampler = MonteCarloSampler(odd, seed=0)
    first, second = sampler.sample(3), sampler.sample(2)
    assert [s.id for s in first + second] == ["s00000", "s00001", "s00002", "s00003", "s00004"]
    assert len({s.seed for s in first + second}) == 5


def test_stratified_hits_every_stratum(odd_doc):
    odd_doc["constraints"] = []
    from verdy.odd import validate_odd

    odd = validate_odd(odd_doc)
    n = 20
    scenarios = StratifiedSampler(odd, seed=0).sample(n)
    dist = StratifiedSampler(odd, seed=0).nominal["person_delay"]
    strata = sorted(int(dist.cdf(s.params["person_delay"]) * n) for s in scenarios)
    assert strata == list(range(n))


def test_infeasible_constraints_raise(odd_doc):
    from verdy.odd import validate_odd

    odd_doc["constraints"] = ["lighting < 0"]
    with pytest.raises(SamplingError):
        MonteCarloSampler(validate_odd(odd_doc), max_rejections=50).sample(1)


def test_importance_weights_are_unbiased(odd):
    """E_m[w] = 1 under the sampling mixture, so mean weight ~ 1 after adaptation."""
    sampler = ImportanceSampler(odd, seed=0)
    first = sampler.sample(200)
    assert all(s.weight == pytest.approx(1.0) for s in first)
    # Pretend dim lighting is dangerous: proposal should move toward low lux.
    sampler.update(first, [s.params["lighting"] / 1000 - 0.05 for s in first])
    batch = sampler.sample(4000)
    weights = np.array([s.weight for s in batch])
    assert weights.max() <= 1 / sampler.defensive + 1e-9
    assert weights.mean() == pytest.approx(1.0, abs=0.12)
    nominal_low = np.mean([s.params["lighting"] < 100 for s in first])
    adapted_low = np.mean([s.params["lighting"] < 100 for s in batch])
    assert adapted_low > nominal_low


def test_replay_sampler(tmp_path, odd):
    for i in range(3):
        (tmp_path / f"log{i}.json").write_text(
            json.dumps({"params": {"lighting": 100 + i, "extra": 1}, "trace": {}})
        )
    sampler = LogReplaySampler(odd, tmp_path)
    assert len(sampler) == 3
    batch = sampler.sample(10)
    assert [s.params for s in batch] == [{"lighting": 100}, {"lighting": 101}, {"lighting": 102}]
    assert batch[0].metadata["log_path"].endswith("log0.json")
    assert sampler.sample(5) == []


def test_make_sampler(odd):
    assert isinstance(make_sampler("stratified", odd), StratifiedSampler)
    with pytest.raises(ValueError):
        make_sampler("grid", odd)
