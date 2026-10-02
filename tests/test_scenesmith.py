import json
import sys
import textwrap
from types import SimpleNamespace

import pytest
import yaml

from verdy import VerdictConfig, evaluate
from verdy.backends.scenesmith import (
    ClaudePromptWriter,
    PolicyOutcome,
    SceneSmithBackend,
    TemplatePromptWriter,
)
from verdy.config import ConfigError, load_run_config
from verdy.metrics import STLSpec
from verdy.odd import validate_odd
from verdy.sampler import MonteCarloSampler
from verdy.secrets import SecretError

FAKE_OPENAI = "sk-" + "proj-" + "Q1w2E3r4" * 5
FAKE_ANTHROPIC = "sk-ant-" + "api03-" + "P0o9I8u7" * 6

FAKE_MAIN = '''
import csv, json, os, sys
from pathlib import Path
args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
out = Path(args["hydra.run.dir"])
rows = list(csv.reader(open(args["experiment.csv_path"])))
assert rows[0] == ["scene_index", "prompt"], rows[0]
prompt = rows[1][1]
print("using key", os.environ.get("OPENAI_API_KEY"))  # a careless log line
if "FAIL" in prompt:
    print("generation exploded for key", os.environ.get("OPENAI_API_KEY"))
    sys.exit(3)
house = out / "scene_000" / "combined_house"
house.mkdir(parents=True)
(house / "house.dmd.yaml").write_text("directives: []\\n# prompt: " + prompt + "\\n")
(house / "house_state.json").write_text(json.dumps({"objects": ["apple", "table"]}))
(out / "env_seen.json").write_text(json.dumps(sorted(os.environ)))
counter = out.parent / "generations.txt"
counter.write_text(counter.read_text() + "x" if counter.exists() else "x")
'''

FAKE_VALIDATOR = '''
import os
from types import SimpleNamespace

class Score:
    def __init__(self, v): self.v = v
    def to_float(self): return self.v

async def validate_task(task_description, cfg, scene_state_path, dmd_path, blender_server,
                        scene_dir):
    print("validator key", os.environ.get("OPENAI_API_KEY"))
    placed = "placed: true" in open(dmd_path).read()
    reqs = [SimpleNamespace(description="apple on table", score=Score(1.0 if placed else 0.0),
                            reasoning="checked pose"),
            SimpleNamespace(description="nothing knocked over", score=Score(1.0), reasoning="ok")]
    score = sum(r.score.to_float() for r in reqs) / len(reqs)
    return SimpleNamespace(overall_success=score >= 0.9, overall_score=score,
                           overall_reasoning=f"model={cfg['model']}", requirements=reqs)
'''


@pytest.fixture
def scenesmith_dir(tmp_path):
    root = tmp_path / "scenesmith"
    (root / "scenesmith" / "robot_eval" / "success_validation").mkdir(parents=True)
    (root / "main.py").write_text(FAKE_MAIN)
    (root / "scenesmith" / "__init__.py").write_text("")
    (root / "scenesmith" / "robot_eval" / "__init__.py").write_text(
        "def create_robot_eval_config(model=None):\n    return {'model': model or 'default'}\n")
    (root / "scenesmith" / "robot_eval" / "success_validation" / "__init__.py").write_text("")
    (root / "scenesmith" / "robot_eval" / "success_validation" / "validator_agent.py"
     ).write_text(FAKE_VALIDATOR)
    return root


@pytest.fixture
def keys(monkeypatch, tmp_path):
    monkeypatch.setenv("VERDY_SECRETS_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    monkeypatch.setenv("UNRELATED_TOKEN", "do-not-pass-me")


ODD = validate_odd({
    "name": "kitchen", "version": "1",
    "parameters": [
        {"name": "clutter", "category": "environment", "type": "categorical",
         "values": ["low", "high"], "description": "Objects on counters"},
        {"name": "lighting", "category": "environment", "type": "continuous",
         "range": [50, 800], "unit": "lux"},
    ],
})
SPECS = [STLSpec("task_done", "always(task_success >= 0.5)"),
         STLSpec("partial", "always(requirements_met >= 0.5)", severity="minor")]
TEMPLATE = "A kitchen with {clutter} clutter, lit at {lighting:.0f} lux. Task: {task}"


class PlacingPolicy:
    """Succeeds in low clutter only; records a small trace of its own."""

    def run(self, scene, output_dmd, seed):
        text = scene.dmd.read_text()
        if "low clutter" in scene.prompt:
            text += "placed: true\n"
        output_dmd.write_text(text)
        return PolicyOutcome(trace={"time": [0.0, 0.5, 1.0], "min_clearance": [0.3, 0.2, 0.4]},
                             info={"grasps": 1})


def backend(scenesmith_dir, tmp_path, **kw):
    return SceneSmithBackend(
        task="Put the apple on the table", scenesmith_dir=scenesmith_dir, python=sys.executable,
        prompt=TemplatePromptWriter(TEMPLATE), cache_dir=tmp_path / "cache", vision=False,
        extra_env={"WANDB_MODE": "disabled"}, **kw)


def test_end_to_end(scenesmith_dir, tmp_path, keys):
    be = backend(scenesmith_dir, tmp_path, validator_model="gpt-test")
    result = evaluate(ODD, SPECS, be, PlacingPolicy(), MonteCarloSampler(ODD, seed=1), 6,
                      VerdictConfig(max_failure_prob=0.5))
    assert not [r.error for r in result.runs if r.error]
    for r in result.runs:
        low = r.scenario.params["clutter"] == "low"
        assert r.failed == (not low)
        assert r.robustness["task_done"] == pytest.approx(0.5 if low else -0.5)
        info = r.backend_info
        assert info["prompt"].startswith(f"A kitchen with {r.scenario.params['clutter']}")
        assert info["validation"]["overall_reasoning"] == "model=gpt-test"
        assert info["grasps"] == 1 and "policy_seconds" in info

    report = result.report
    assert report["inputs"]["backend_config"]["task"] == "Put the apple on the table"
    creds = report["inputs"]["credentials"]
    assert creds["fingerprint"] == "sha256"
    assert creds["secrets"]["OPENAI_API_KEY"]["fingerprint"].startswith("sha256:")
    assert creds["secrets"]["GOOGLE_API_KEY"] == {"set": False}

    # Keys never reach the report or the stored logs, even though SceneSmith printed them.
    text = json.dumps(report)
    assert FAKE_OPENAI not in text and FAKE_ANTHROPIC not in text
    logs = list((tmp_path / "cache").rglob("*.log"))
    assert logs and all(FAKE_OPENAI not in p.read_text() for p in logs)
    assert any("[REDACTED]" in p.read_text() for p in logs)

    # SceneSmith only sees the secrets it needs.
    seen = json.loads(next((tmp_path / "cache").rglob("env_seen.json")).read_text())
    assert "OPENAI_API_KEY" in seen and "WANDB_MODE" in seen
    assert "ANTHROPIC_API_KEY" not in seen and "UNRELATED_TOKEN" not in seen


def test_scenes_are_cached(scenesmith_dir, tmp_path, keys):
    be = backend(scenesmith_dir, tmp_path)
    scenario = {"id": "s0", "params": {"clutter": "low", "lighting": 300.0}, "seed": 1}
    first = be.build(scenario)
    second = be.build({**scenario, "id": "s1"})
    assert not first.cached and second.cached and first.cache_key == second.cache_key
    assert (tmp_path / "cache" / "scenes" / "generations.txt").read_text() == "x"
    assert first.dmd.name == "house.dmd.yaml" and first.state.name == "house_state.json"


def test_missing_openai_key_fails_before_generating(scenesmith_dir, tmp_path, keys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(SecretError, match="OPENAI_API_KEY"):
        evaluate(ODD, SPECS, backend(scenesmith_dir, tmp_path), PlacingPolicy(),
                 MonteCarloSampler(ODD), 1)
    assert not (tmp_path / "cache" / "scenes").exists()


def test_generation_failure_is_recorded_and_redacted(scenesmith_dir, tmp_path, keys):
    be = SceneSmithBackend(
        task="t", scenesmith_dir=scenesmith_dir, python=sys.executable, vision=False,
        prompt=TemplatePromptWriter("FAIL {clutter}"), cache_dir=tmp_path / "cache")
    result = evaluate(ODD, SPECS, be, PlacingPolicy(), MonteCarloSampler(ODD), 2)
    assert all(r.error and r.failed for r in result.runs)
    assert "main.py exited with 3" in result.runs[0].error
    assert FAKE_OPENAI not in result.runs[0].error and "[REDACTED]" in result.runs[0].error


def test_policy_must_write_scene(scenesmith_dir, tmp_path, keys):
    be = backend(scenesmith_dir, tmp_path)
    result = evaluate(ODD, SPECS, be, lambda scene, out, seed: None,
                      MonteCarloSampler(ODD), 1)
    assert "did not write the final scene" in result.runs[0].error


def test_extra_env_rejects_credentials(scenesmith_dir, tmp_path):
    with pytest.raises(ValueError, match="must not contain credentials"):
        SceneSmithBackend(task="t", scenesmith_dir=scenesmith_dir, python=sys.executable,
                          extra_env={"KEY": FAKE_OPENAI})


def test_not_a_checkout(tmp_path):
    with pytest.raises(ValueError, match="not a SceneSmith checkout"):
        SceneSmithBackend(task="t", scenesmith_dir=tmp_path)


def fake_claude(*prompts):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        text = json.dumps({"prompt": prompts[len(calls) - 1]})
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=text)])

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))), calls


def test_claude_prompt_writer_caches(tmp_path):
    client, calls = fake_claude("A cluttered kitchen at dusk.", "unused")
    writer = ClaudePromptWriter(cache_dir=tmp_path, client=client)
    params = {"clutter": "high", "lighting": 120.0}
    assert writer.write(params, ODD, "Put the apple away") == "A cluttered kitchen at dusk."
    assert writer.write(params, ODD, "Put the apple away") == "A cluttered kitchen at dusk."
    assert len(calls) == 1
    user = calls[0]["messages"][0]["content"]
    assert "Put the apple away" in user and '"unit": "lux"' in user
    assert '"meaning": "Objects on counters"' in user
    assert calls[0]["model"] == "claude-opus-5-5"
    assert writer.secret_names == ("ANTHROPIC_API_KEY",)


def test_claude_writer_uses_secret(monkeypatch, tmp_path):
    anthropic = pytest.importorskip("anthropic")
    monkeypatch.setenv("VERDY_SECRETS_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    seen = {}
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: seen.update(kw) or "client")
    from verdy.llm import claude_client

    assert claude_client() == "client" and seen == {"api_key": FAKE_ANTHROPIC}


def test_run_config(scenesmith_dir, tmp_path, keys):
    (tmp_path / "odd.yaml").write_text(yaml.safe_dump(ODD.to_dict()))
    (tmp_path / "specs.yaml").write_text(yaml.safe_dump({"specs": [s.to_dict() for s in SPECS]}))
    (tmp_path / "policy.py").write_text(textwrap.dedent('''
        class Policy:
            def run(self, scene, output_dmd, seed):
                output_dmd.write_text(scene.dmd.read_text() + "placed: true\\n")
    '''))
    cfg = {
        "odd": "odd.yaml", "specs": "specs.yaml", "policy": "policy:Policy",
        "backend": {"type": "scenesmith", "options": {
            "task": "Put the apple on the table", "scenesmith_dir": str(scenesmith_dir),
            "python": sys.executable, "cache_dir": "cache", "vision": False,
            "prompt": {"writer": "template", "template": TEMPLATE}}},
        "runs": 2, "credentials": {"fingerprint": "none"},
    }
    (tmp_path / "run.yaml").write_text(yaml.safe_dump(cfg))
    loaded = load_run_config(tmp_path / "run.yaml")
    assert loaded.backend.cache_dir == tmp_path / "cache" and loaded.fingerprint == "none"
    result = evaluate(loaded.odd, loaded.specs, loaded.backend, loaded.policy, loaded.sampler,
                      loaded.runs, loaded.verdict, fingerprint=loaded.fingerprint)
    assert result.status == "INCONCLUSIVE" and not any(r.failed for r in result.runs)
    assert result.report["inputs"]["credentials"]["secrets"]["OPENAI_API_KEY"] == {
        "set": True, "source": "env", "fingerprint": None}

    cfg["backend"]["options"]["api_key"] = FAKE_OPENAI
    (tmp_path / "run.yaml").write_text(yaml.safe_dump(cfg))
    with pytest.raises(ConfigError, match="backend.options.api_key"):
        load_run_config(tmp_path / "run.yaml")


def test_requires_scenesmith_environment(scenesmith_dir):
    with pytest.raises(ValueError, match="uv sync"):
        SceneSmithBackend(task="t", scenesmith_dir=scenesmith_dir)
