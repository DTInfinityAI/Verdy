import pytest

from verdy import evaluate
from verdy.backends import FunctionBackend, HomeNavSim
from verdy.backends.sim2d import cautious_policy
from verdy.ledger import verify_report
from verdy.sampler import ImportanceSampler, MonteCarloSampler, StratifiedSampler
from verdy.verdict import Status, VerdictConfig


def run(odd, specs, sampler, n=40, **kwargs):
    return evaluate(odd, specs, HomeNavSim(horizon=20.0), cautious_policy(), sampler, n,
                    VerdictConfig(max_failure_prob=0.2), **kwargs)


def test_end_to_end(odd, nav_specs):
    result = run(odd, nav_specs, StratifiedSampler(odd, seed=1), keep_traces=True)
    assert len(result.runs) == 40 and len(result.traces) == 40
    assert result.status in {s.value for s in Status}
    report = result.report
    assert verify_report(report)["digest_ok"]
    assert report["results"]["n_runs"] == 40
    assert set(report["results"]["per_spec"]) == {"no_collision", "slow_near_person", "reach_goal"}
    assert report["inputs"]["sampler"]["sampler"] == "StratifiedSampler"
    run0 = report["runs"][0]
    assert set(run0["robustness"]) == set(report["results"]["per_spec"])
    assert run0["trace_sha256"]
    assert "Verdict:" in result.summary()


def test_reproducible(odd, nav_specs):
    a = run(odd, nav_specs, MonteCarloSampler(odd, seed=5), n=10)
    b = run(odd, nav_specs, MonteCarloSampler(odd, seed=5), n=10)
    def strip(runs):
        return [{k: v for k, v in r.items() if k != "duration_s"} for r in runs]

    assert strip(a.report["runs"]) == strip(b.report["runs"])


def test_minor_specs_do_not_fail_runs(odd, nav_specs):
    result = run(odd, nav_specs, MonteCarloSampler(odd, seed=0), n=30)
    for r in result.runs:
        assert r.failed == bool({"no_collision", "slow_near_person"} & set(r.violated))


def test_errors_are_recorded(odd, nav_specs):
    def rollout(env, policy, seed):
        if seed % 2:
            raise RuntimeError("simulator crashed")
        return {"time": [0, 0.1], "dist_obstacle": [1, 1], "speed": [0, 0], "dist_goal": [0, 0]}

    backend = FunctionBackend(lambda p, s: None, rollout)
    result = evaluate(odd, nav_specs, backend, None, MonteCarloSampler(odd, seed=0), 20)
    errors = [r for r in result.runs if r.error]
    assert errors and all("simulator crashed" in r.error and r.failed for r in errors)
    assert result.report["results"]["n_errors"] == len(errors)
    lenient = evaluate(odd, nav_specs, backend, None, MonteCarloSampler(odd, seed=0), 20,
                       VerdictConfig(errors_as_failures=False))
    assert not any(r.failed for r in lenient.runs)


def test_adaptive_sampler_gets_updates(odd, nav_specs):
    sampler = ImportanceSampler(odd, seed=0)
    run(odd, nav_specs, sampler, n=60, batch_size=20)
    assert sampler.iteration == 3


def test_fail_on_must_match_a_spec(odd, nav_specs):
    with pytest.raises(ValueError):
        evaluate(odd, nav_specs[2:], HomeNavSim(), cautious_policy(),
                 MonteCarloSampler(odd), 1, VerdictConfig())
