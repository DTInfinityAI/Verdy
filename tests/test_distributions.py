import numpy as np
import pytest

from verdy.odd import Parameter
from verdy.sampler import DistributionError, distribution_for


def numeric(dist, rng=(0.0, 10.0), type_="continuous"):
    return Parameter("x", "environment", type_, range=rng, distribution=dist)


@pytest.mark.parametrize(
    "dist", [None, "uniform", "normal(5, 2)", "loguniform", "triangular(2)", "beta(2, 5)"]
)
def test_numeric_distributions_stay_in_range(dist):
    lo, hi = (1.0, 10.0)
    d = distribution_for(numeric(dist, (lo, hi)))
    rng = np.random.default_rng(0)
    xs = [d.sample(rng) for _ in range(500)]
    assert min(xs) >= lo and max(xs) <= hi
    assert all(d.pdf(x) > 0 for x in xs[:20])
    assert d.ppf(0.0) == pytest.approx(lo) and d.ppf(1.0) == pytest.approx(hi)


def test_truncated_normal_mean():
    d = distribution_for(numeric("normal(5, 1)"))
    rng = np.random.default_rng(1)
    assert np.mean([d.sample(rng) for _ in range(4000)]) == pytest.approx(5.0, abs=0.08)


def test_categorical_weights():
    p = Parameter("f", "environment", "categorical", values=["a", "b"], weights=[3, 1])
    d = distribution_for(p)
    assert d.pmf("a") == pytest.approx(0.75)
    assert d.pmf("zzz") == 0.0
    rng = np.random.default_rng(2)
    draws = [d.sample(rng) for _ in range(4000)]
    assert draws.count("a") / 4000 == pytest.approx(0.75, abs=0.03)


def test_boolean():
    d = distribution_for(Parameter("b", "faults", "boolean", distribution="bernoulli(0.1)"))
    assert d.pmf(True) == pytest.approx(0.1)
    assert distribution_for(Parameter("b", "faults", "boolean")).pmf(True) == 0.5


@pytest.mark.parametrize(
    "param",
    [
        numeric("normal(1)"),
        numeric("normal(1, -1)"),
        numeric("triangular(20)"),
        numeric("poisson(3)"),
        numeric("uniform", rng=(5.0, 5.0)),
        Parameter("b", "faults", "boolean", distribution="bernoulli(2)"),
        Parameter("c", "task", "categorical", values=["a"], distribution="normal(0, 1)"),
    ],
)
def test_invalid_distributions(param):
    with pytest.raises(DistributionError):
        distribution_for(param)
