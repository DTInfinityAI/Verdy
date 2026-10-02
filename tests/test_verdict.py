import pytest

from verdy.odd import validate_odd
from verdy.verdict import (
    Status,
    VerdictConfig,
    clopper_pearson,
    compute_coverage,
    decide,
    required_runs,
    weighted_estimate,
)


def test_rule_of_three_and_required_runs():
    est = clopper_pearson(0, 59, 0.95)
    assert est.upper == pytest.approx(1 - 0.05 ** (1 / 59))
    assert est.upper < 0.05 < clopper_pearson(0, 58, 0.95).upper
    assert required_runs(0.05, 0.95) == 59
    assert required_runs(0.01, 0.99) == 459
    with pytest.raises(ValueError):
        required_runs(0, 0.95)


def test_clopper_pearson_bounds():
    est = clopper_pearson(10, 100, 0.95)
    assert est.estimate == 0.1
    assert 0.05 < est.lower < 0.1 < est.upper < 0.17
    assert clopper_pearson(100, 100).upper == 1.0


def test_weighted_estimate_reduces_to_binomial():
    failed = [True] * 3 + [False] * 97
    assert weighted_estimate(failed, [1.0] * 100) == clopper_pearson(3, 100)


def test_weighted_estimate_uses_weights():
    est = weighted_estimate([True, False, False, False], [0.1, 1.0, 1.0, 1.0])
    assert est.estimate == pytest.approx(0.1 / 3.1)
    assert est.method == "importance"
    assert est.lower <= est.estimate <= est.upper
    assert 1 < est.effective_n < 4


def test_decide():
    cfg = VerdictConfig(max_failure_prob=0.05, confidence=0.95)
    assert decide(clopper_pearson(0, 100), None, cfg).status is Status.PASS
    assert decide(clopper_pearson(30, 100), None, cfg).status is Status.FAIL
    inconclusive = decide(clopper_pearson(0, 20), None, cfg)
    assert inconclusive.status is Status.INCONCLUSIVE
    assert any("59 runs" in r for r in inconclusive.reasons)
    strict = VerdictConfig(max_failure_prob=0.05, min_runs=500)
    assert decide(clopper_pearson(0, 100), None, strict).status is Status.INCONCLUSIVE


def test_decide_requires_coverage(odd):
    cov = compute_coverage(odd, [{"lighting": 25, "floor_type": "tile"}])
    cfg = VerdictConfig(max_failure_prob=0.05, min_coverage=0.8)
    verdict = decide(clopper_pearson(0, 100), cov, cfg)
    assert verdict.status is Status.INCONCLUSIVE and "coverage" in verdict.reasons[0]


@pytest.mark.parametrize("kwargs", [{"max_failure_prob": 0}, {"confidence": 1.0},
                                    {"min_coverage": 2}])
def test_verdict_config_validation(kwargs):
    with pytest.raises(ValueError):
        VerdictConfig(**kwargs)


def test_coverage():
    odd = validate_odd({
        "name": "c", "version": "1",
        "parameters": [
            {"name": "x", "category": "task", "type": "continuous", "range": [0, 10]},
            {"name": "f", "category": "task", "type": "categorical", "values": ["a", "b"]},
        ],
    })
    rows = [{"x": 0.5, "f": "a"}, {"x": 9.9, "f": "a"}, {"x": 10.0, "f": "a"}]
    cov = compute_coverage(odd, rows, bins=5)
    assert cov.per_parameter == {"x": 0.4, "f": 0.5}
    assert cov.uncovered["f"] == ["'b'"]
    assert cov.uncovered["x"] == ["[2, 4]", "[4, 6]", "[6, 8]"]
    assert cov.overall == pytest.approx(0.45)
    assert cov.pairwise == pytest.approx(2 / 10)
