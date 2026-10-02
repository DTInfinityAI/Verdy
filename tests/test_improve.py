import json
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
import yaml
from conftest import EXAMPLES

from verdy.backends import HomeNavSim
from verdy.cli import main
from verdy.improve import (
    BradleyTerryRewardModel,
    CLILabeler,
    CommandTrainer,
    Episode,
    FailureFocusedCurriculum,
    FileLabeler,
    ImprovementLoop,
    ParameterizedPolicy,
    ParameterSearchTrainer,
    Preference,
    PreferenceReward,
    SafetyMarginReward,
    ScriptedLabeler,
    SplitConfig,
    TrainingContext,
    TrainingData,
    failure_map,
    load_demonstrations,
    record_demonstrations,
    rollout,
    run_features,
    select_pairs,
)
from verdy.improve.config import load_loop_config
from verdy.improve.loop import CertificationResult, _promote
from verdy.metrics import STLSpec, load_specs
from verdy.odd import load_odd
from verdy.sampler import MonteCarloSampler
from verdy.sampler.base import Scenario
from verdy.verdict import VerdictConfig

HOME = EXAMPLES / "home_robot"
sys.path.insert(0, str(HOME))
from policy import make_policy  # noqa: E402

SPECS = [STLSpec("safe", "always(d >= 0)", severity="critical"),
         STLSpec("goal", "eventually(g <= 0)", severity="minor")]


def ep(i, robustness, params=None, error=None, trace=None):
    trace = trace or {"time": [0.0, 0.1, 0.2], "speed": [0.0, 0.5, 0.4]}
    return Episode(Scenario(f"s{i}", params or {}, i), robustness,
                   [k for k, v in robustness.items() if v < 0],
                   any(v < 0 for v in robustness.values()), error, trace,
                   run_features(robustness, trace))


# -- rewards ------------------------------------------------------------------------------


def test_safety_margin_reward():
    r = SafetyMarginReward(SPECS, margin=0.5, violation_penalty=1.0)
    full = r(ep(0, {"safe": 2.0, "goal": 2.0}))
    scrape = r(ep(1, {"safe": 0.01, "goal": 2.0}))
    fail = r(ep(2, {"safe": -0.2, "goal": 2.0}))
    assert full == pytest.approx(1.0)
    assert fail < 0 < scrape < full  # scraping a pass earns far less than a real margin
    assert r(ep(3, {"safe": 1.0}, error="boom")) == -2.0
    assert r(ep(4, {"safe": -100.0, "goal": -100.0})) == pytest.approx(-2.0)
    with pytest.raises(ValueError):
        SafetyMarginReward(SPECS, margin=0)(ep(0, {"safe": 1.0, "goal": 1.0}))


def test_preference_reward_is_bounded_and_calibrated():
    class Model:
        fitted = True

        def predict(self, f):
            return 1000.0 * f["rob:safe"]

    episodes = [ep(i, {"safe": float(i), "goal": 1.0}) for i in range(5)]
    pref = PreferenceReward(Model()).calibrate(episodes)
    values = [pref(e) for e in episodes]
    assert all(-1 < v < 1 for v in values) and values == sorted(values)
    assert abs(np.mean(values)) < 0.2
    assert PreferenceReward(Model())(ep(9, {"safe": 1.0}, error="x")) == 0.0


# -- preferences ----------------------------------------------------------------------------


def synthetic_prefs(n, rng, noise=0.0):
    prefs = []
    for k in range(n):
        fa = {"smooth": float(rng.normal()), "noise": float(rng.normal())}
        fb = {"smooth": float(rng.normal()), "noise": float(rng.normal())}
        better_a = fa["smooth"] + rng.normal(0, noise) > fb["smooth"]
        prefs.append(Preference(f"p{k}", "a", "b", "a" if better_a else "b", "test", fa, fb))
    return prefs


def test_bradley_terry_learns_the_preferred_direction():
    rng = np.random.default_rng(0)
    model = BradleyTerryRewardModel(l2=0.1).fit(synthetic_prefs(200, rng, noise=0.2))
    assert model.fitted and model.train_accuracy > 0.85
    w = model.to_dict()["weights"]
    assert list(w)[0] == "smooth" and w["smooth"] > 0 and abs(w["noise"]) < w["smooth"] / 3
    assert model.prob_a_preferred({"smooth": 2.0}, {"smooth": -2.0}) > 0.9
    assert not BradleyTerryRewardModel(min_preferences=10).fit(synthetic_prefs(5, rng)).fitted


def test_select_pairs():
    rng = np.random.default_rng(0)
    eps = [ep(i, {"safe": float(i % 4), "goal": 1.0}) for i in range(10)]
    eps.append(ep(99, {}, error="crash"))
    r = SafetyMarginReward(SPECS)
    pairs = select_pairs(eps, 4, rng, r)
    assert len(pairs) == 4
    ids = [e.id for pair in pairs for e in pair]
    assert len(ids) == len(set(ids)) and "s99" not in ids
    assert all(abs(r(a) - r(b)) < 1e-9 for a, b in pairs[:2])  # most ambiguous first


def test_file_labeler_round_trip(tmp_path):
    eps = [ep(i, {"safe": 1.0, "goal": 1.0}) for i in range(4)]
    labeler = FileLabeler(tmp_path / "fb")
    pairs = [(eps[0], eps[1]), (eps[2], eps[3])]
    assert labeler.label(pairs, tag="c1-") == []
    pending = [json.loads(x) for x in (tmp_path / "fb" / "pending.jsonl").read_text().splitlines()]
    assert [p["pair_id"] for p in pending] == ["c1-s0~s1", "c1-s2~s3"]
    assert Path(pending[0]["a"]["trace_file"]).is_file()
    (tmp_path / "fb" / "labels.jsonl").write_text(
        json.dumps({"pair_id": "c1-s0~s1", "choice": "b", "reason": "smoother"}) + "\n")
    got = labeler.label(pairs, tag="c1-")
    assert len(got) == 1 and got[0].choice == "b" and got[0].reason == "smoother"
    assert len((tmp_path / "fb" / "pending.jsonl").read_text().splitlines()) == 2


def test_cli_labeler():
    answers = iter(["x", "a", "s", "t"])
    lab = CLILabeler(input_fn=lambda _: next(answers))
    eps = [ep(i, {"safe": 1.0, "goal": 1.0}) for i in range(6)]
    got = lab.label([(eps[0], eps[1]), (eps[2], eps[3]), (eps[4], eps[5])])
    assert [p.choice for p in got] == ["a", "tie"]


def test_scripted_labeler_validates():
    with pytest.raises(ValueError):
        ScriptedLabeler(lambda a, b: "maybe").label([(ep(0, {}), ep(1, {}))])


# -- targeted practice ----------------------------------------------------------------------


def test_failure_map_and_curriculum(odd):
    eps = []
    for s in MonteCarloSampler(odd, seed=0).sample(400):
        lux = s.params["lighting"]
        rob = {"safe": (lux - 100) / 500}  # dim light fails
        eps.append(Episode(s, rob, [], rob["safe"] < 0, None, None))
    fmap = failure_map(odd, eps)
    assert fmap["weakest"][0]["parameter"] == "lighting"
    assert fmap["lighting"][0]["cell"].startswith("[20,")
    cur = FailureFocusedCurriculum(odd, focus=0.8, seed=5)
    cur.fit(eps)
    train = cur.sample(300, prefix="c1-")
    assert train[0].id.startswith("c1-1-")
    nominal = np.mean([e.scenario.params["lighting"] < 100 for e in eps])
    focused = np.mean([s.params["lighting"] < 100 for s in train])
    assert focused > nominal + 0.15


# -- demonstrations and trainers ---------------------------------------------------------


@pytest.fixture(scope="module")
def home():
    return load_odd(HOME / "odd.yaml"), load_specs(HOME / "specs.yaml")


def test_demonstrations_and_imitation_pretraining(tmp_path, home):
    odd, specs = home
    expert = make_policy(cruise=0.7, slow_radius=2.5, stop_radius=1.0, memory=0.0)
    path = tmp_path / "demos.jsonl"
    n = record_demonstrations(odd, HomeNavSim(horizon=8.0), expert,
                              MonteCarloSampler(odd, seed=1).sample(3), path, every=2)
    steps = load_demonstrations(path)
    assert n == len(steps) > 50 and {"obs", "action", "scenario", "session"} <= set(steps[0])
    start = ParameterizedPolicy(make_policy, {"cruise": 1.1, "slow_radius": 1.0,
                                              "stop_radius": 0.4, "memory": 0.0})
    trainer = ParameterSearchTrainer(
        {"cruise": [0.4, 1.2], "slow_radius": [0.8, 3.5], "stop_radius": [0.3, 1.5]},
        iterations=4, population=10, seed=0)
    ctx = TrainingContext(odd, specs, HomeNavSim(), lambda e: 0.0, tmp_path)
    tuned = trainer.pretrain(start, steps, ctx)
    info = trainer.last_pretrain
    assert info["imitation_mse_after"] < info["imitation_mse_before"] / 3
    assert abs(tuned.params["cruise"] - 0.7) < 0.15
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"obs": {}}\n')
    with pytest.raises(ValueError, match="'obs' and 'action'"):
        load_demonstrations(bad)


def test_parameter_search_needs_parameterized_policy(tmp_path, home):
    odd, specs = home
    ctx = TrainingContext(odd, specs, HomeNavSim(), lambda e: 0.0, tmp_path)
    with pytest.raises(TypeError, match="ParameterizedPolicy"):
        ParameterSearchTrainer({"cruise": [0, 1]}).train(make_policy(), None, ctx)


LOADER = '''
import json
from pathlib import Path

class Loaded:
    def __init__(self, params):
        self.params = params
    def act(self, obs):
        return (0.0, 0.0)

def load(out_dir):
    return Loaded(json.loads((Path(out_dir) / "policy.json").read_text()))
'''

TRAIN_SCRIPT = '''
import json, os, sys
from pathlib import Path
data, out = Path(sys.argv[1]), Path(sys.argv[2])
eps = [json.loads(l) for l in (data / "episodes.jsonl").read_text().splitlines()]
prefs = (data / "preferences.jsonl").read_text().splitlines()
(out / "policy.json").write_text(json.dumps({
    "episodes": len(eps), "preferences": len(prefs),
    "scenarios": len((data / "scenarios.jsonl").read_text().splitlines()),
    "has_key": "TRAINER_TOKEN" in os.environ, "leaked": "UNRELATED" in os.environ,
    "cycle": sys.argv[3]}))
'''


def test_command_trainer(tmp_path, home, monkeypatch):
    odd, specs = home
    monkeypatch.setenv("VERDY_SECRETS_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("TRAINER_TOKEN", "x" * 20)
    monkeypatch.setenv("UNRELATED", "y")
    (tmp_path / "train.py").write_text(TRAIN_SCRIPT)
    (tmp_path / "loader_mod.py").write_text(LOADER)
    sys.path.insert(0, str(tmp_path))
    from loader_mod import load

    eps, _ = rollout(odd, specs, HomeNavSim(horizon=5.0), make_policy(),
                     MonteCarloSampler(odd, seed=2).sample(3))
    data = TrainingData(2, eps, {e.id: 0.5 for e in eps}, [], BradleyTerryRewardModel(),
                        MonteCarloSampler(odd, seed=3).sample(4))
    trainer = CommandTrainer(
        [sys.executable, "train.py", "{data_dir}", "{out_dir}", "{cycle}"], load,
        cwd=tmp_path, secrets=["TRAINER_TOKEN"])
    ctx = TrainingContext(odd, specs, HomeNavSim(), lambda e: 0.0, tmp_path / "work")
    policy = trainer.train(make_policy(), data, ctx)
    assert policy.params == {"episodes": 3, "preferences": 0, "scenarios": 4,
                             "has_key": True, "leaked": False, "cycle": "2"}
    data_dir = tmp_path / "work" / "cycle_2" / "data"
    assert {"episodes.jsonl", "manifest.json", "reward_model.json", "traces"} <= {
        p.name for p in data_dir.iterdir()}
    failing = CommandTrainer([sys.executable, "-c", "import sys; sys.exit(4)"], load,
                             cwd=tmp_path)
    with pytest.raises(RuntimeError, match="exited with 4"):
        failing.train(make_policy(), data, ctx)


# -- the loop ------------------------------------------------------------------------------


def cert(status, p, per_spec=None):
    return CertificationResult(status, p, p, p, 100, "r.json", "d", per_spec or {})


def test_promotion_rules():
    assert _promote(cert("PASS", 0.01), cert("INCONCLUSIVE", 0.04), "no_worse")[0]
    assert not _promote(cert("FAIL", 0.01), cert("INCONCLUSIVE", 0.04), "no_worse")[0]
    assert not _promote(cert("PASS", 0.03), cert("PASS", 0.02), "no_worse")[0]
    assert _promote(cert("PASS", 0.02), cert("PASS", 0.02), "no_worse")[0]
    assert not _promote(cert("PASS", 0.02), cert("PASS", 0.02), "better")[0]
    ok, why = _promote(cert("PASS", 0.0, {"goal": 0.05}), cert("PASS", 0.02, {"goal": 0.01}),
                       "no_worse", {"goal": 0.02})
    assert not ok and "goal regressed" in why


def small_loop(home, tmp_path, **kw):
    odd, specs = home
    policy = ParameterizedPolicy(make_policy, {"cruise": 1.0, "slow_radius": 1.5,
                                               "stop_radius": 0.6, "memory": 0.3})
    trainer = ParameterSearchTrainer({"cruise": [0.5, 1.2], "slow_radius": [0.8, 3.5]},
                                     iterations=1, population=4, seed=0)
    return ImprovementLoop(
        odd, specs, HomeNavSim(), policy, trainer, VerdictConfig(max_failure_prob=0.05),
        tmp_path / "out", diagnose=SplitConfig(runs=40, seed=1),
        certify=SplitConfig(runs=60, seed=2), training_scenarios=20, pairs_per_cycle=8,
        labeler=ScriptedLabeler(lambda a, b: "a" if a.features["dist_obstacle:min"]
                                >= b.features["dist_obstacle:min"] else "b"), **kw)


def test_loop_end_to_end(tmp_path, home):
    loop = small_loop(home, tmp_path)
    result = loop.run(2)
    assert len(result.cycles) == 2
    c1 = result.cycles[0]
    assert c1.feedback["pairs_asked"] == 8 and c1.feedback["total_preferences"] == 8
    assert c1.training["scenarios"] + c1.training["removed_overlap_with_certification"] == 20
    assert c1.training["rollouts"] > 0
    assert result.cycles[1].feedback["total_preferences"] == 16
    for c in result.cycles:  # candidate and incumbent judged on the same fresh held-out set
        assert c.candidate.runs == c.incumbent.runs == 60
        assert Path(c.candidate.report_path).is_file()
        assert c.promoted == _promote(c.candidate, c.incumbent, "no_worse")[0]
    out = tmp_path / "out"
    assert len((out / "preferences.jsonl").read_text().splitlines()) == 16
    report = json.loads((out / "loop.report.json").read_text())
    from verdy.ledger import verify_report

    assert verify_report(report)["digest_ok"]
    assert report["inputs"]["kind"] == "improvement_loop"
    assert report["results"]["final"] == result.final.__dict__
    assert "Final certified policy" in result.summary()


def test_loop_validates_splits_and_guard(tmp_path, home):
    odd, specs = home
    with pytest.raises(ValueError, match="different seeds"):
        ImprovementLoop(odd, specs, HomeNavSim(), None, None, VerdictConfig(), tmp_path,
                        diagnose=SplitConfig(10, seed=1), certify=SplitConfig(10, seed=1))
    with pytest.raises(ValueError, match="unknown specs"):
        small_loop(home, tmp_path, guard={"nope": 0.1})


def test_loop_config_and_cli(tmp_path, capsys):
    cfg = {
        "evaluation": str(HOME / "run.yaml"),
        "policy": {"factory": f"{HOME / 'policy.py'}:make_policy",
                   "params": {"cruise": 1.0, "slow_radius": 1.5}},
        "cycles": 1,
        "diagnose": {"runs": 30, "seed": 3}, "certify": {"runs": 40, "seed": 4},
        "reward": {"safety": {"weight": 1.0, "margin": 0.3}, "preference": {"weight": 0.3}},
        "feedback": {"labeler": {"type": "file", "directory": "fb"}, "pairs_per_cycle": 3},
        "curriculum": {"focus": 0.5, "scenarios": 10},
        "trainer": {"type": "parameter_search",
                    "options": {"bounds": {"cruise": [0.5, 1.2]}, "iterations": 1,
                                "population": 3}},
        "guard": {"reach_goal": 0.05},
        "output": "out",
    }
    path = tmp_path / "improve.yaml"
    path.write_text(yaml.safe_dump(cfg))
    loaded = load_loop_config(path)
    assert loaded.cycles == 1 and loaded.loop.guard == {"reach_goal": 0.05}
    assert isinstance(loaded.loop.labeler, FileLabeler)
    code = main(["improve", str(path), "-q"])
    assert code in (0, 1, 3)
    assert "Final certified policy" in capsys.readouterr().out
    assert (tmp_path / "fb" / "pending.jsonl").is_file()  # operators have pairs to label
    cfg["trainer"]["options"]["api_key"] = "sk-" + "proj-" + "K3y5" * 8
    path.write_text(yaml.safe_dump(cfg))
    assert main(["improve", str(path)]) == 2


def test_example_loop_config_loads():
    loaded = load_loop_config(EXAMPLES / "home_robot" / "improve.yaml")
    assert loaded.loop.demonstrations and loaded.loop.guard == {"reach_goal": 0.01}
    assert isinstance(loaded.loop.policy, ParameterizedPolicy)


def test_custom_trainer_plugs_in(tmp_path, home):
    (tmp_path / "my_rl.py").write_text(textwrap.dedent('''
        class MyTrainer:
            def __init__(self, scale=1.0):
                self.scale = scale
                self.calls = 0
            def train(self, policy, data, ctx):
                self.calls += 1
                assert data.scenarios and data.episodes and ctx.reward
                return policy
    '''))
    cfg = {"evaluation": str(HOME / "run.yaml"), "cycles": 1,
           "diagnose": {"runs": 20, "seed": 5}, "certify": {"runs": 20, "seed": 6},
           "curriculum": {"scenarios": 5},
           "trainer": {"type": "my_rl:MyTrainer", "options": {"scale": 2.0}},
           "output": "out"}
    (tmp_path / "loop.yaml").write_text(yaml.safe_dump(cfg))
    loaded = load_loop_config(tmp_path / "loop.yaml")
    result = loaded.loop.run(1)
    assert loaded.loop.trainer.calls == 1 and loaded.loop.trainer.scale == 2.0
    assert result.cycles[0].promoted  # same policy: no worse on the same held-out set
