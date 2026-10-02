import pytest

from verdy.metrics import SpecError, STLEvaluator, STLSpec, infer_signals, parse_specs
from verdy.monitor import RuntimeMonitor, monitor_formula


def trace(**signals):
    n = len(next(iter(signals.values())))
    return {"time": [i * 0.5 for i in range(n)], **signals}


def test_infer_signals():
    assert infer_signals("always[0:5s]((d <= 0.5) implies (speed <= 0.3))") == ["d", "speed"]
    assert infer_signals("eventually[0:20](dist_goal <= 0.2) and not rise(x > 1)") == [
        "dist_goal", "x"]


def test_robustness_values():
    ev = STLEvaluator([
        STLSpec("safe", "always(d >= 0.3)"),
        STLSpec("arrive", "eventually[0:1](g <= 0.5)"),
    ])
    rob = ev.robustness(trace(d=[2, 1, 0.5, 0.4], g=[3, 1, 0.8, 0.4]))
    assert rob["safe"] == pytest.approx(0.1)
    assert rob["arrive"] == pytest.approx(-0.3)  # bounds are seconds: g at t <= 1.0 is >= 0.8


def test_negative_robustness_is_violation():
    ev = STLEvaluator([STLSpec("safe", "always(d >= 0.3)")])
    assert ev.robustness(trace(d=[1, 0.1, 1]))["safe"] == pytest.approx(-0.2)


def test_missing_signal():
    ev = STLEvaluator([STLSpec("safe", "always(d >= 0.3)")])
    with pytest.raises(SpecError, match="missing signals"):
        ev.robustness(trace(x=[1, 1]))


def test_parse_specs_errors():
    with pytest.raises(SpecError, match="cannot parse"):
        parse_specs({"specs": [{"name": "bad", "formula": "always(d >=)"}]})
    with pytest.raises(SpecError, match="duplicate"):
        parse_specs({"specs": [{"name": "a", "formula": "always(d >= 0)"}] * 2})
    with pytest.raises(SpecError, match="invalid specs file"):
        parse_specs({"specs": [{"name": "a", "formula": "always(d >= 0)", "severity": "big"}]})


def test_example_specs_load():
    from conftest import EXAMPLES

    from verdy.metrics import load_specs

    specs = load_specs(EXAMPLES / "home_robot" / "specs.yaml")
    assert [s.severity for s in specs] == ["critical", "major", "minor"]
    assert specs[1].signals == ["dist_obstacle", "speed"]


def test_monitor_formula_conversion():
    assert monitor_formula(STLSpec("a", "always(d >= 0)")) == "historically(d >= 0)"
    assert monitor_formula(STLSpec("a", "always((d <= 1) implies (v <= 2))")) == (
        "historically((d <= 1) implies (v <= 2))")
    assert monitor_formula(STLSpec("a", "x", monitor="once(d >= 0)")) == "once(d >= 0)"
    for formula in ["eventually[0:5](g <= 0)", "always(d >= 0) and always(v <= 1)",
                    "always(eventually[0:1](d >= 0))"]:
        with pytest.raises(SpecError):
            monitor_formula(STLSpec("a", formula))


def test_runtime_monitor_detects_violation(nav_specs):
    monitor = RuntimeMonitor(nav_specs, dt=0.1, skip_unmonitorable=True)
    assert monitor.skipped == ["reach_goal"]
    statuses = [
        monitor.update({"dist_obstacle": d, "speed": v})
        for d, v in [(2.0, 1.0), (1.0, 0.5), (0.4, 0.2), (0.3, 0.6), (1.0, 0.1)]
    ]
    assert [s.ok for s in statuses] == [True, True, True, False, False]  # historically latches
    assert statuses[3].violated == ["slow_near_person"]
    assert statuses[3].step == 3
    with pytest.raises(SpecError):
        RuntimeMonitor(nav_specs, dt=0.1)
    with pytest.raises(SpecError, match="missing signal"):
        monitor.update({"dist_obstacle": 1.0})
